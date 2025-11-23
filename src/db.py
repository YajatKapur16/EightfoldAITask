import sqlite3
import json
from datetime import datetime
import uuid

DB_NAME = "chat_history.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (id TEXT PRIMARY KEY, name TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
    conn.commit()
    conn.close()

def create_session(name):
    session_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("INSERT INTO sessions (id, name, timestamp) VALUES (?, ?, ?)", (session_id, name, timestamp))
    conn.commit()
    conn.close()
    return session_id

def get_sessions():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, name, timestamp FROM sessions ORDER BY timestamp DESC")
    sessions = [{"id": row[0], "name": row[1], "timestamp": row[2]} for row in c.fetchall()]
    conn.close()
    return sessions

def get_messages(session_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (session_id,))
    messages = [{"role": r, "content": c} for r, c in c.fetchall()]
    conn.close()
    return messages

def delete_session(session_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

def update_session_name(session_id, new_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE sessions SET name = ? WHERE id = ?", (new_name, session_id))
    conn.commit()
    conn.close()

def get_last_empty_session():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Get most recent session
    c.execute("SELECT id, name FROM sessions ORDER BY timestamp DESC LIMIT 1")
    row = c.fetchone()
    if row:
        session_id, name = row
        # Check message count
        c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,))
        count = c.fetchone()[0]
        conn.close()
        if count == 0 and name == "New Session":
            return session_id
    conn.close()
    return None
