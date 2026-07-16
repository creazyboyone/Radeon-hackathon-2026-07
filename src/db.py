import json
import sqlite3
import time
import uuid
import logging

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, parent_id TEXT, type TEXT, trigger TEXT,
    status TEXT, summary TEXT, started_at INTEGER, ended_at INTEGER
);
CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, seq INTEGER,
    kind TEXT, content_json TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS cluster_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_json TEXT, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, tool_name TEXT, args_json TEXT,
    risk_level TEXT, status TEXT, result_json TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT, tool_name TEXT, args_json TEXT,
    risk_level TEXT, dry_run_json TEXT,
    status TEXT, decided_by TEXT, decided_at INTEGER, ts INTEGER
);
"""


class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def create_session(self, session_type="fix", parent_id=None, trigger=""):
        sid = str(uuid.uuid4())[:8]
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO sessions(id,parent_id,type,trigger,status,started_at) VALUES(?,?,?,?,?,?)",
            (sid, parent_id, session_type, trigger, "running", now),
        )
        self.conn.commit()
        return sid

    def log_event(self, session_id: str, seq: int, kind: str, content: dict):
        self.conn.execute(
            "INSERT INTO session_events(session_id,seq,kind,content_json,ts) VALUES(?,?,?,?,?)",
            (session_id, seq, kind, json.dumps(content, ensure_ascii=False), int(time.time())),
        )
        self.conn.commit()

    def finish_session(self, session_id: str, summary: str, status="done"):
        self.conn.execute(
            "UPDATE sessions SET status=?, summary=?, ended_at=? WHERE id=?",
            (status, summary, int(time.time()), session_id),
        )
        self.conn.commit()

    def save_state_card(self, snapshot: dict):
        self.conn.execute(
            "INSERT INTO cluster_state(snapshot_json, updated_at) VALUES(?,?)",
            (json.dumps(snapshot, ensure_ascii=False), int(time.time())),
        )
        self.conn.commit()

    def get_latest_state_card(self) -> dict:
        row = self.conn.execute(
            "SELECT snapshot_json FROM cluster_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                pass
        return {}
