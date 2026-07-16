"""FastAPI 后端 — REST API + WebSocket 事件推送"""
import asyncio
import json
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .event_bus import bus


def create_app(store) -> FastAPI:
    """创建 FastAPI 应用, 注入 Store"""
    app = FastAPI(title="AIOps Agent Console")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- REST API ----

    @app.get("/api/sessions")
    def list_sessions():
        rows = store.conn.execute(
            "SELECT id, parent_id, type, status, trigger, "
            "started_at, ended_at FROM sessions "
            "ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
        return [
            {"id": r[0], "parent_id": r[1], "type": r[2], "status": r[3],
             "trigger": r[4], "started_at": r[5], "ended_at": r[6] or 0}
            for r in rows
        ]

    @app.get("/api/sessions/{sid}/events")
    def get_events(sid: str):
        rows = store.conn.execute(
            "SELECT seq, kind, content_json, ts FROM session_events "
            "WHERE session_id=? ORDER BY seq", (sid,)
        ).fetchall()
        return [
            {"seq": r[0], "kind": r[1],
             "content": json.loads(r[2]) if r[2] else {},
             "ts": r[3]}
            for r in rows
        ]

    @app.get("/api/approvals")
    def list_approvals(status: str = ""):
        sql = ("SELECT id, session_id, tool_name, args_json, risk_level, "
               "dry_run_json, status, decided_by, ts FROM approvals")
        params = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY ts DESC"
        rows = store.conn.execute(sql, params).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "tool_name": r[2],
             "args": json.loads(r[3]) if r[3] else {},
             "risk_level": r[4],
             "dry_run": json.loads(r[5]) if r[5] else {},
             "status": r[6], "decided_by": r[7] or "", "ts": r[8]}
            for r in rows
        ]

    @app.post("/api/approvals/{aid}/decide")
    def decide_approval(aid: str, decision: dict):
        status = decision.get("status", "rejected")
        decided_by = decision.get("decided_by", "web-user")
        store.conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=? "
            "WHERE id=?",
            (status, decided_by, int(time.time()), aid)
        )
        store.conn.commit()
        bus.publish({"type": "approval_decision", "id": aid,
                      "status": status, "decided_by": decided_by})
        return {"id": aid, "status": status}

    @app.get("/api/audit")
    def audit_log(limit: int = 100):
        rows = store.conn.execute(
            "SELECT id, session_id, tool_name, args_json, risk_level, "
            "status, result_json, ts FROM audit_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "tool_name": r[2],
             "args": json.loads(r[3]) if r[3] else {},
             "risk_level": r[4], "status": r[5],
             "result": json.loads(r[6]) if r[6] else {},
             "ts": r[7]}
            for r in rows
        ]

    @app.get("/api/cluster/snapshot")
    def cluster_snapshot():
        from .tools import get_cluster_snapshot as snap
        return snap()

    # ---- WebSocket ----

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        q = bus.subscribe()
        import queue as _q
        try:
            while True:
                try:
                    msg = q.get_nowait()
                    await websocket.send_text(msg)
                except _q.Empty:
                    await asyncio.sleep(0.3)
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return app
