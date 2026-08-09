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
# 3. Streamlit Interface
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Support Ticket Portal", page_icon="🎫", layout="wide")
    st.title("🎫 Support Ticket Portal")

    if not init_database():
        st.stop()

    tab_view, tab_new_ticket = st.tabs(["📋 View & Manage Tickets", "➕ Create New Ticket"])

    # TAB 1: View Tickets & Messages
    with tab_view:
        st.subheader("All Support Tickets")
        tickets_df = get_all_tickets()

        if tickets_df.empty:
            st.info("No tickets found in Lakebase.")
        else:
            st.dataframe(tickets_df, use_container_width=True)
            st.divider()

            ticket_ids = tickets_df["ticket_id"].tolist()
            selected_ticket_id = st.selectbox("Select Ticket ID to View Details:", ticket_ids)

            if selected_ticket_id:
                selected_ticket = tickets_df[tickets_df["ticket_id"] == selected_ticket_id].iloc[0]

                col1, col2 = st.columns([2, 1])

                with col1:
                    st.markdown(f"### Ticket #{selected_ticket['ticket_id']}: {selected_ticket['title']}")
                    st.caption(f"Created by **{selected_ticket['created_by']}** on {selected_ticket['created_at']}")

                with col2:
                    current_status = selected_ticket["status"]
                    status_options = ["open", "in_progress", "resolved", "closed"]

                    new_status = st.selectbox(
                        "Update Status:",
                        status_options,
                        index=status_options.index(current_status) if current_status in status_options else 0,
                        key=f"status_{selected_ticket_id}"
                    )

                    if new_status != current_status:
                        if st.button("Save Status Change"):
                            update_ticket_status(selected_ticket_id, new_status)
                            st.success(f"Status updated to '{new_status}'!")
                            st.rerun()

                st.subheader("💬 Messages")
                messages_df = get_ticket_messages(selected_ticket_id)

                if messages_df.empty:
                    st.info("No messages for this ticket yet.")
                else:
                    for _, msg in messages_df.iterrows():
                        with st.chat_message("user"):
                            st.write(f"**{msg['author']}** · *{msg['created_at']}*")
                            st.write(msg["message_text"])

                st.markdown("#### Add a Message")
                with st.form(f"add_message_form_{selected_ticket_id}"):
                    author_input = st.text_input("Your Email/Name")
                    message_input = st.text_area("Message Content")
                    submit_msg = st.form_submit_button("Send Message")

                    if submit_msg:
                        if not author_input.strip() or not message_input.strip():
                            st.error("Please fill in both Author and Message fields.")
                        else:
                            add_message(selected_ticket_id, author_input.strip(), message_input.strip())
                            st.success("Message posted!")
                            st.rerun()

    # TAB 2: Create New Ticket
    with tab_new_ticket:
        st.subheader("Create a New Support Ticket")

        with st.form("create_ticket_form"):
            new_title = st.text_input("Ticket Title")
            new_created_by = st.text_input("Your Email")
            initial_status = st.selectbox("Status", ["open", "in_progress", "resolved"], index=0)
            initial_message = st.text_area("Initial Description/Message")

            submit_ticket = st.form_submit_button("Submit Ticket", type="primary")

            if submit_ticket:
                if not new_title.strip() or not new_created_by.strip() or not initial_message.strip():
                    st.error("Please complete all required fields.")
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