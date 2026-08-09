import os
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import streamlit as st

# -----------------------------------------------------------------------------
# 1. Lakebase Postgres Connection Configuration
# -----------------------------------------------------------------------------
# Lakebase exposes a standard PostgreSQL endpoint.
# Retrieve credentials from Lakebase secrets or App Environment Variables.

def get_lakebase_connection():
    return psycopg2.connect(
        host=os.getenv("LAKEBASE_HOST"),
        port=os.getenv("LAKEBASE_PORT", "5432"),
        dbname=os.getenv("LAKEBASE_DATABASE", "postgres"),
        user=os.getenv("LAKEBASE_USER"),
        password=os.getenv("LAKEBASE_PASSWORD"),
        sslmode="require"
    )

def run_query(query: str, params: tuple = None) -> pd.DataFrame:
    """Executes SELECT queries against Lakebase and returns a DataFrame."""
    conn = get_lakebase_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params or ())
            results = cursor.fetchall()
            return pd.DataFrame(results)
    finally:
        conn.close()

def execute_statement(statement: str, params: tuple = None):
    """Executes INSERT, UPDATE, or DELETE statements against Lakebase."""
    conn = get_lakebase_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(statement, params or ())
        conn.commit()
    finally:
        conn.close()

# -----------------------------------------------------------------------------
# 2. UI Configuration & Header
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Lakebase Support Ticket Portal", page_icon="🎫", layout="wide")
st.title("🎫 Support Ticket System (Powered by Lakebase)")

tab_view, tab_new_ticket = st.tabs(["📋 View & Manage Tickets", "➕ Create New Ticket"])

# -----------------------------------------------------------------------------
# TAB 1: View Tickets, Messages, Update Status, & Add Message
# -----------------------------------------------------------------------------
with tab_view:
    st.subheader("All Support Tickets")
    
    # Requirement: View all support tickets from Lakebase
    tickets_df = run_query("""
        SELECT ticket_id, title, status, created_by, created_at 
        FROM ticket_sys.tickets 
        ORDER BY created_at DESC
    """)
    
    if tickets_df.empty:
        st.info("No tickets found in Lakebase.")
    else:
        st.dataframe(tickets_df, use_container_width=True)
        st.divider()
        
        # Requirement: Select a ticket
        ticket_ids = tickets_df["ticket_id"].tolist()
        selected_ticket_id = st.selectbox("Select Ticket ID to View Details:", ticket_ids)
        
        if selected_ticket_id:
            selected_ticket = tickets_df[tickets_df["ticket_id"] == selected_ticket_id].iloc[0]
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown(f"### Ticket #{selected_ticket['ticket_id']}: {selected_ticket['title']}")
                st.caption(f"Created by **{selected_ticket['created_by']}** on {selected_ticket['created_at']}")
            
            with col2:
                # Requirement: Update a ticket's status in Lakebase
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
                        execute_statement(
                            "UPDATE ticket_sys.tickets SET status = %s WHERE ticket_id = %s",
                            (new_status, int(selected_ticket_id))
                        )
                        st.success(f"Status updated to '{new_status}' in Lakebase!")
                        st.rerun()

            st.subheader("💬 Ticket Messages")
            
            # Requirement: View messages for selected ticket
            messages_df = run_query(
                """
                SELECT message_id, author, message_text, created_at 
                FROM ticket_sys.ticket_messages 
                WHERE ticket_id = %s 
                ORDER BY created_at ASC
                """,
                (int(selected_ticket_id),)
            )
            
            if messages_df.empty:
                st.info("No messages for this ticket yet.")
            else:
                for _, msg in messages_df.iterrows():
                    with st.chat_message("user"):
                        st.write(f"**{msg['author']}** · *{msg['created_at']}*")
                        st.write(msg["message_text"])

            # Requirement: Add a message to an existing ticket in Lakebase
            st.markdown("#### Add a Message")
            with st.form(f"add_message_form_{selected_ticket_id}"):
                author_input = st.text_input("Your Email/Name")
                message_input = st.text_area("Message Content")
                submit_msg = st.form_submit_button("Send Message")
                
                if submit_msg:
                    if not author_input or not message_input:
                        st.error("Please fill in both Author and Message fields.")
                    else:
                        # Auto-increment using Lakebase COALESCE or native SERIAL
                        max_msg_id = run_query("SELECT COALESCE(MAX(message_id), 0) + 1 AS next_id FROM ticket_sys.ticket_messages")
                        next_msg_id = int(max_msg_id.iloc[0]["next_id"])
                        
                        execute_statement(
                            """
                            INSERT INTO ticket_sys.ticket_messages (message_id, ticket_id, message_text, author, created_at)
                            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                            """,
                            (next_msg_id, int(selected_ticket_id), message_input, author_input)
                        )
                        st.success("Message committed to Lakebase!")
                        st.rerun()

# -----------------------------------------------------------------------------
# TAB 2: Create New Ticket in Lakebase
# -----------------------------------------------------------------------------
with tab_new_ticket:
    st.subheader("Create a New Support Ticket")
    
    with st.form("create_ticket_form"):
        new_title = st.text_input("Ticket Title")
        new_created_by = st.text_input("Your Email")
        initial_status = st.selectbox("Status", ["open", "in_progress", "resolved"], index=0)
        initial_message = st.text_area("Initial Description/Message")
        
        submit_ticket = st.form_submit_button("Submit Ticket")
        
        if submit_ticket:
            if not new_title or not new_created_by or not initial_message:
                st.error("Please complete all required fields.")
            else:
                # Retrieve next IDs from Lakebase
                max_t_id = run_query("SELECT COALESCE(MAX(ticket_id), 0) + 1 AS next_id FROM ticket_sys.tickets")
                next_ticket_id = int(max_t_id.iloc[0]["next_id"])
                
                max_m_id = run_query("SELECT COALESCE(MAX(message_id), 0) + 1 AS next_id FROM ticket_sys.ticket_messages")
                next_msg_id = int(max_m_id.iloc[0]["next_id"])
                
                # Write new ticket to Lakebase
                execute_statement(
                    """
                    INSERT INTO ticket_sys.tickets (ticket_id, title, status, created_by, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (next_ticket_id, new_title, initial_status, new_created_by)
                )
                
                # Write initial message to Lakebase
                execute_statement(
                    """
                    INSERT INTO ticket_sys.ticket_messages (message_id, ticket_id, message_text, author, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (next_msg_id, next_ticket_id, initial_message, new_created_by)
                )
                
                st.success(f"Ticket #{next_ticket_id} created successfully in Lakebase!")
                st.rerun()