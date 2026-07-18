"""FastAPI 后端 — REST API + WebSocket 事件推送"""
import asyncio
import json
import queue
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .event_bus import bus


def create_app(store) -> FastAPI:
    """创建 FastAPI 应用, 注入 Store"""
    from .config import CONSOLE_TOKEN

    app = FastAPI(title="AIOps Agent Console")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 可选认证中间件 ----
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if CONSOLE_TOKEN:
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token != CONSOLE_TOKEN:
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)


    # ---- REST API ----

    @app.get("/api/sessions")
    def list_sessions():
        with store.lock:
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
        with store.lock:
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
        with store.lock:
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
        with store.lock:
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
        with store.lock:
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

    # ---- 风险规则 CRUD (§21.3 / T7) ----

    @app.get("/api/risk_rules")
    def list_risk_rules(enabled: str = ""):
        return store.get_risk_rules(enabled_only=(enabled.lower() == "true"))

    @app.post("/api/risk_rules")
    def create_risk_rule(rule: dict):
        rid = store.upsert_risk_rule(rule)
        return {"id": rid, "status": "created"}

    @app.put("/api/risk_rules/{rid}")
    def update_risk_rule(rid: str, rule: dict):
        rule["id"] = rid
        store.upsert_risk_rule(rule)
        return {"id": rid, "status": "updated"}

    @app.delete("/api/risk_rules/{rid}")
    def delete_risk_rule(rid: str):
        store.delete_risk_rule(rid)
        return {"id": rid, "status": "deleted"}

    # ---- WebSocket ----

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        q = bus.subscribe()
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    # 阻塞式获取, 通过 executor 运行避免阻塞事件循环
                    msg = await loop.run_in_executor(None, lambda: q.get(timeout=1))
                    await websocket.send_text(msg)
                except queue.Empty:
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return app
