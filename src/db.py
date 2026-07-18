import json
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS risk_rules (
    id          TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    match_json  TEXT,
    tier        TEXT NOT NULL,
    autonomous  INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER,
    updated_by  TEXT
);
"""

# 默认风险规则种子数据 (§21.3 — 首启若空则灌入)
_DEFAULT_RISK_RULES = [
    ("rule_get_service_status", "get_service_status", None, "low",      1, 1, 0),
    ("rule_get_alerts",         "get_alerts",         None, "low",      1, 1, 0),
    ("rule_get_metrics",       "get_metrics",        None, "low",      1, 1, 0),
    ("rule_read_logs",         "read_logs",          None, "low",      1, 1, 0),
    ("rule_search_kb",         "search_kb",          None, "low",      1, 1, 0),
    ("rule_hdfs_admin",        "hdfs_admin",         None, "low",      1, 1, 0),
    ("rule_restart_service",   "restart_service",    None, "recover",  1, 1, 0),
    ("rule_edit_remote_config","edit_remote_config", None, "reversible", 1, 1, 0),
    # fail-closed 默认: 未知工具一律不可逆 + 不自动
    ("rule_default",           "*",                  None, "irreversible", 0, 1, -1),
]


class Store:
    """SQLite 存储 — 线程安全 (RLock 保护所有 conn 操作)"""

    def __init__(self, db_path: str):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self.conn.executescript(
            "CREATE INDEX IF NOT EXISTS idx_events_session "
            "ON session_events(session_id, seq);"
            "CREATE INDEX IF NOT EXISTS idx_audit_session "
            "ON audit_log(session_id);"
            "CREATE INDEX IF NOT EXISTS idx_audit_tool "
            "ON audit_log(tool_name, ts);"
            "CREATE INDEX IF NOT EXISTS idx_approvals_session "
            "ON approvals(session_id);"
            "CREATE INDEX IF NOT EXISTS idx_approvals_status "
            "ON approvals(status);"
            "CREATE INDEX IF NOT EXISTS idx_risk_rules_tool "
            "ON risk_rules(tool_name, enabled, priority DESC);"
        )
        self._seed_risk_rules()
        self.conn.commit()

    @property
    def lock(self):
        return self._lock

    # ---- session ----

    def create_session(self, session_type="fix", parent_id=None, trigger=""):
        sid = str(uuid.uuid4())[:8]
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions(id,parent_id,type,trigger,status,started_at) VALUES(?,?,?,?,?,?)",
                (sid, parent_id, session_type, trigger, "running", now),
            )
            self.conn.commit()
        return sid

    def log_event(self, session_id: str, seq: int, kind: str, content: dict):
        with self._lock:
            self.conn.execute(
                "INSERT INTO session_events(session_id,seq,kind,content_json,ts) VALUES(?,?,?,?,?)",
                (session_id, seq, kind, json.dumps(content, ensure_ascii=False), int(time.time())),
            )
            self.conn.commit()

    def finish_session(self, session_id: str, summary: str, status="done"):
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET status=?, summary=?, ended_at=? WHERE id=?",
                (status, summary, int(time.time()), session_id),
            )
            self.conn.commit()

    def save_state_card(self, snapshot: dict):
        with self._lock:
            self.conn.execute(
                "INSERT INTO cluster_state(snapshot_json, updated_at) VALUES(?,?)",
                (json.dumps(snapshot, ensure_ascii=False), int(time.time())),
            )
            self.conn.commit()

    def get_latest_state_card(self) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT snapshot_json FROM cluster_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                pass
        return {}

    # ---- risk_rules CRUD (§21.3) ----

    def _seed_risk_rules(self):
        """首启若空则灌入默认规则"""
        count = self.conn.execute("SELECT COUNT(*) FROM risk_rules").fetchone()[0]
        if count > 0:
            return
        for rid, tool, match_json, tier, auto, enabled, pri in _DEFAULT_RISK_RULES:
            self.conn.execute(
                "INSERT INTO risk_rules(id,tool_name,match_json,tier,autonomous,"
                "enabled,priority,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, tool, match_json, tier, auto, enabled, pri,
                 int(time.time()), "seed"),
            )
        logger.info(f"Seeded {len(_DEFAULT_RISK_RULES)} default risk_rules")

    def get_risk_rules(self, enabled_only=False):
        """获取所有风险规则"""
        sql = "SELECT id,tool_name,match_json,tier,autonomous,enabled,priority,updated_at,updated_by FROM risk_rules"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority DESC, tool_name"
        with self._lock:
            rows = self.conn.execute(sql).fetchall()
        return [
            {"id": r[0], "tool_name": r[1],
             "match_json": json.loads(r[2]) if r[2] else None,
             "tier": r[3], "autonomous": bool(r[4]),
             "enabled": bool(r[5]), "priority": r[6],
             "updated_at": r[7] or 0, "updated_by": r[8] or ""}
            for r in rows
        ]

    def upsert_risk_rule(self, rule: dict):
        """插入或更新风险规则"""
        rid = rule.get("id") or str(uuid.uuid4())[:8]
        match_json = json.dumps(rule["match_json"], ensure_ascii=False) if rule.get("match_json") else None
        # §21.3 UI 护栏: irreversible 档禁止 autonomous=1
        autonomous = 0 if rule.get("tier") == "irreversible" else (1 if rule.get("autonomous") else 0)
        with self._lock:
            self.conn.execute(
                "INSERT INTO risk_rules(id,tool_name,match_json,tier,autonomous,"
                "enabled,priority,updated_at,updated_by) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "tool_name=excluded.tool_name, match_json=excluded.match_json, "
                "tier=excluded.tier, autonomous=excluded.autonomous, "
                "enabled=excluded.enabled, priority=excluded.priority, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (rid, rule["tool_name"], match_json, rule["tier"],
                 autonomous, 1 if rule.get("enabled", True) else 0,
                 rule.get("priority", 0), int(time.time()),
                 rule.get("updated_by", "web-user"))
            )
            self.conn.commit()
        return rid

    def delete_risk_rule(self, rule_id: str):
        """删除风险规则"""
        with self._lock:
            self.conn.execute("DELETE FROM risk_rules WHERE id=?", (rule_id,))
            self.conn.commit()

    # ---- audit_log 查询 (供 §21.5 attempt 节流) ----

    def count_audit_attempts(self, tool_name, target, window_sec=600):
        """统计观察窗口内某 (tool, target) 的已执行次数, 供 attempt 节流"""
        cutoff = int(time.time()) - window_sec
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE tool_name=? AND status='executed' AND ts>? "
                "AND json_extract(args_json, '$.service')=?",
                (tool_name, cutoff, target)
            ).fetchone()
            last_ts_row = self.conn.execute(
                "SELECT MAX(ts) FROM audit_log "
                "WHERE tool_name=? AND status='executed' "
                "AND json_extract(args_json, '$.service')=?",
                (tool_name, target)
            ).fetchone()
        count = row[0] if row else 0
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else 0
        return count, last_ts