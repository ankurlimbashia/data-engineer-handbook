import os
import streamlit as st
import pandas as pd
import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool
from databricks import sdk

# -----------------------------------------------------------------------------
# 1. Lakebase Native OAuth Connection Setup
# -----------------------------------------------------------------------------
workspace_client = sdk.WorkspaceClient()


class OAuthConnection(psycopg.Connection):
    """Connection subclass that auto-refreshes OAuth credentials for Lakebase."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        endpoint = os.getenv("PGENDPOINT", "")
        credential = workspace_client.postgres.generate_database_credential(
            endpoint=endpoint
        )
        kwargs["password"] = credential.token
        return super().connect(conninfo, **kwargs)


@st.cache_resource
def get_connection_pool():
    """Cache connection pool across Streamlit script reruns."""
    conn_string = (
        f"dbname={os.getenv('PGDATABASE', 'databricks_postgres')} "
        f"user={os.getenv('PGUSER')} "
        f"host={os.getenv('PGHOST')} "
        f"port={os.getenv('PGPORT', '5432')} "
        f"sslmode={os.getenv('PGSSLMODE', 'require')} "
        f"application_name={os.getenv('PGAPPNAME', 'ticket_app')}"
    )
    return ConnectionPool(
        conn_string,
        connection_class=OAuthConnection,
        min_size=0,
        max_size=10,
        timeout=15.0
    )


def get_connection():
    """Get a connection from the pool."""
    return get_connection_pool().connection()


def get_schema_name():
    """Target schema name for tickets."""
    return "ticket_sys"


# -----------------------------------------------------------------------------
# 2. Database Operations (CRUD)
# -----------------------------------------------------------------------------
def init_database():
    """Initialize schema and tables if they do not exist."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                schema_name = get_schema_name()

                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))

                # Tickets table
                cur.execute(sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {}.tickets (
                        ticket_id INT PRIMARY KEY,
                        title VARCHAR(500) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'open',
                        created_by VARCHAR(200) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """).format(sql.Identifier(schema_name)))

                # Ticket Messages table
                cur.execute(sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {}.ticket_messages (
                        message_id INT PRIMARY KEY,
                        ticket_id INT NOT NULL REFERENCES {}.tickets(ticket_id),
                        message_text VARCHAR(1000) NOT NULL,
                        author VARCHAR(100) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """).format(sql.Identifier(schema_name), sql.Identifier(schema_name)))

                conn.commit()
                return True
    except Exception as e:
        st.error(f"Error initializing Lakebase tables: {e}")
        return False


def get_all_tickets():
    """Fetch all support tickets."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            query = sql.SQL("""
                SELECT ticket_id, title, status, created_by, created_at 
                FROM {}.tickets 
                ORDER BY created_at DESC
            """).format(sql.Identifier(schema))
            cur.execute(query)
            cols = [desc[0] for desc in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)


def get_ticket_messages(ticket_id):
    """Fetch messages for a selected ticket."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            query = sql.SQL("""
                SELECT message_id, author, message_text, created_at 
                FROM {}.ticket_messages 
                WHERE ticket_id = %s 
                ORDER BY created_at ASC
            """).format(sql.Identifier(schema))
            cur.execute(query, (ticket_id,))
            cols = [desc[0] for desc in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)


def create_ticket(title, status, created_by, initial_message):
    """Create a new ticket and its initial message."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()

            cur.execute(sql.SQL("SELECT COALESCE(MAX(ticket_id), 0) + 1 FROM {}.tickets").format(sql.Identifier(schema)))
            next_t_id = cur.fetchone()[0]

            cur.execute(sql.SQL("SELECT COALESCE(MAX(message_id), 0) + 1 FROM {}.ticket_messages").format(sql.Identifier(schema)))
            next_m_id = cur.fetchone()[0]

            cur.execute(
                sql.SQL("INSERT INTO {}.tickets (ticket_id, title, status, created_by) VALUES (%s, %s, %s, %s)")
                .format(sql.Identifier(schema)),
                (next_t_id, title, status, created_by)
            )

            cur.execute(
                sql.SQL("INSERT INTO {}.ticket_messages (message_id, ticket_id, message_text, author) VALUES (%s, %s, %s, %s)")
                .format(sql.Identifier(schema)),
                (next_m_id, next_t_id, initial_message, created_by)
            )
            conn.commit()
            return next_t_id


def add_message(ticket_id, author, message_text):
    """Add a message to an existing ticket."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()

            cur.execute(sql.SQL("SELECT COALESCE(MAX(message_id), 0) + 1 FROM {}.ticket_messages").format(sql.Identifier(schema)))
            next_m_id = cur.fetchone()[0]

            cur.execute(
                sql.SQL("INSERT INTO {}.ticket_messages (message_id, ticket_id, message_text, author) VALUES (%s, %s, %s, %s)")
                .format(sql.Identifier(schema)),
                (next_m_id, ticket_id, message_text, author)
            )
            conn.commit()


def update_ticket_status(ticket_id, new_status):
    """Update status of a ticket."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            cur.execute(
                sql.SQL("UPDATE {}.tickets SET status = %s WHERE ticket_id = %s")
                .format(sql.Identifier(schema)),
                (new_status, ticket_id)
            )
            conn.commit()


# -----------------------------------------------------------------------------
# 3. Custom UI Styling Helpers
# -----------------------------------------------------------------------------
def get_status_badge(status):
    """Returns formatted HTML badges for ticket status."""
    badges = {
        "open": "🔴 <span style='color: #ef4444; font-weight: bold;'>OPEN</span>",
        "in_progress": "🟡 <span style='color: #f59e0b; font-weight: bold;'>IN PROGRESS</span>",
        "resolved": "🟢 <span style='color: #10b981; font-weight: bold;'>RESOLVED</span>",
        "closed": "⚪ <span style='color: #6b7280; font-weight: bold;'>CLOSED</span>",
    }
    return badges.get(status.lower(), status)


# -----------------------------------------------------------------------------
# 4. Streamlit Interface
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Support Ticket Portal", page_icon="🎫", layout="wide")

    # Header section
    st.title("🎫 Support Ticket Portal")
    st.caption("Databricks Lakebase Powered Operations Dashboard")

    if not init_database():
        st.stop()

    # Load all tickets
    tickets_df = get_all_tickets()

    # -------------------------------------------------------------------------
    # Feature 1: Ticket Statistics Dashboard
    # -------------------------------------------------------------------------
    st.markdown("### 📊 Ticket Statistics")
    
    total_count = len(tickets_df)
    open_count = len(tickets_df[tickets_df["status"] == "open"]) if not tickets_df.empty else 0
    in_prog_count = len(tickets_df[tickets_df["status"] == "in_progress"]) if not tickets_df.empty else 0
    resolved_count = len(tickets_df[tickets_df["status"] == "resolved"]) if not tickets_df.empty else 0
    closed_count = len(tickets_df[tickets_df["status"] == "closed"]) if not tickets_df.empty else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Tickets", total_count)
    m2.metric("Open", open_count, delta_color="inverse")
    m3.metric("In Progress", in_prog_count)
    m4.metric("Resolved", resolved_count)
    m5.metric("Closed", closed_count)

    st.divider()

    # App Tabs
    tab_view, tab_new_ticket = st.tabs(["📋 View & Manage Tickets", "➕ Create New Ticket"])

    # -------------------------------------------------------------------------
    # TAB 1: View, Filter & Manage Tickets
    # -------------------------------------------------------------------------
    with tab_view:
        if tickets_df.empty:
            st.info("No tickets found in Lakebase. Create one using the 'Create New Ticket' tab!")
        else:
            # Feature 2: Filtering Controls
            st.markdown("#### 🔍 Filter & Search")
            col_filter, col_search = st.columns([1, 2])

            with col_filter:
                status_filter = st.multiselect(
                    "Filter by Status:",
                    options=["open", "in_progress", "resolved", "closed"],
                    default=["open", "in_progress", "resolved", "closed"]
                )

            with col_search:
                search_query = st.text_input("Search Title or Creator:", placeholder="Type to filter...")

            # Apply Filters
            filtered_df = tickets_df[tickets_df["status"].isin(status_filter)]
            if search_query.strip():
                query = search_query.strip().lower()
                filtered_df = filtered_df[
                    filtered_df["title"].str.lower().str.contains(query) |
                    filtered_df["created_by"].str.lower().str.contains(query)
                ]

            st.write(f"Showing **{len(filtered_df)}** of **{total_count}** tickets")
            st.dataframe(filtered_df, use_container_width=True)

            st.divider()

            # Ticket Selection & Details
            if not filtered_df.empty:
                ticket_ids = filtered_df["ticket_id"].tolist()
                selected_ticket_id = st.selectbox("Select Ticket ID to View Details & Messages:", ticket_ids)

                if selected_ticket_id:
                    selected_ticket = filtered_df[filtered_df["ticket_id"] == selected_ticket_id].iloc[0]

                    # Feature 3: Visual Container for Ticket Details
                    with st.container(border=True):
                        c_header, c_status = st.columns([3, 1])

                        with c_header:
                            st.markdown(f"## Ticket #{selected_ticket['ticket_id']}: {selected_ticket['title']}")
                            st.markdown(f"**Submitted by:** `{selected_ticket['created_by']}` | **Date:** {selected_ticket['created_at']}")

                        with c_status:
                            st.markdown(f"**Current Status:** {get_status_badge(selected_ticket['status'])}", unsafe_allow_html=True)
                            
                            status_options = ["open", "in_progress", "resolved", "closed"]
                            current_status = selected_ticket["status"]
                            new_status = st.selectbox(
                                "Update Status:",
                                status_options,
                                index=status_options.index(current_status) if current_status in status_options else 0,
                                key=f"status_select_{selected_ticket_id}"
                            )

                            if new_status != current_status:
                                if st.button("Save Status Change", type="primary"):
                                    update_ticket_status(selected_ticket_id, new_status)
                                    st.success(f"Status updated to '{new_status}'!")
                                    st.rerun()

                    # Message Thread Section
                    st.markdown("### 💬 Communication Log")
                    messages_df = get_ticket_messages(selected_ticket_id)

                    if messages_df.empty:
                        st.info("No messages posted for this ticket yet.")
                    else:
                        for _, msg in messages_df.iterrows():
                            with st.chat_message("user"):
                                st.markdown(f"**{msg['author']}** · *{msg['created_at']}*")
                                st.write(msg["message_text"])

                    # Reply Form inside Card
                    with st.container(border=True):
                        st.markdown("#### ✉️ Post a Reply")
                        with st.form(f"reply_form_{selected_ticket_id}", clear_on_submit=True):
                            reply_author = st.text_input("Your Email/Name")
                            reply_text = st.text_area("Reply Message")
                            submit_reply = st.form_submit_button("Send Reply", type="primary")

                            if submit_reply:
                                if not reply_author.strip() or not reply_text.strip():
                                    st.error("Please provide both your name and a reply message.")
                                else:
                                    add_message(selected_ticket_id, reply_author.strip(), reply_text.strip())
                                    st.success("Reply posted!")
                                    st.rerun()

    # -------------------------------------------------------------------------
    # TAB 2: Create New Ticket
    # -------------------------------------------------------------------------
    with tab_new_ticket:
        st.subheader("➕ Submit a New Support Ticket")

        with st.container(border=True):
            with st.form("create_ticket_form", clear_on_submit=True):
                new_title = st.text_input("Ticket Title", placeholder="Brief description of the issue")
                new_created_by = st.text_input("Your Email", placeholder="user@company.com")
                initial_status = st.selectbox("Initial Status", ["open", "in_progress", "resolved"], index=0)
                initial_message = st.text_area("Detailed Description", placeholder="Describe the steps to reproduce or issue details...")

                submit_ticket = st.form_submit_button("Submit Ticket", type="primary")

                if submit_ticket:
                    if not new_title.strip() or not new_created_by.strip() or not initial_message.strip():
                        st.error("Please fill in all required fields.")
                    else:
                        new_id = create_ticket(
                            new_title.strip(),
                            initial_status,
                            new_created_by.strip(),
                            initial_message.strip()
                        )
                        st.success(f"Ticket #{new_id} created successfully!")
                        st.rerun()


if __name__ == "__main__":
    main()