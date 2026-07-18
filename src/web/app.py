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
    from src.config import CONSOLE_TOKEN

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

    # ---- 知识库 runbooks CRUD (M5) ----
    # 注意: 静态路径 (search/stats) 必须在动态路径 ({rb_id}) 之前定义

    @app.get("/api/runbooks")
    def list_runbooks(status: str = "", source: str = "", keyword: str = ""):
        """查询 runbook 列表 (不返回 embedding)"""
        return store.get_runbooks(
            status=status or None,
            source=source or None,
            keyword=keyword or None,
        )

    @app.get("/api/runbooks/search")
    def search_runbooks(q: str = "", limit: int = 5):
        """知识库检索 (混合向量+BM25, 供前端测试)"""
        if not q:
            return {"query": "", "results": []}
        try:
            import importlib
            kb_module = importlib.import_module("src.kb")
            kb_module.ensure_embeddings(store)
            results = kb_module.hybrid_search(store, q, limit=limit)
        except Exception as e:
            # 降级 FTS
            results = store.search_runbooks_fts(q, limit=limit)
        # 精简返回
        return {
            "query": q,
            "matches": len(results),
            "results": [
                {"id": r.get("id", ""), "title": r["title"],
                 "content": r.get("content", "")[:500],
                 "tags": r.get("tags", ""), "score": r.get("score", 0),
                 "match_type": r.get("match_type", "")}
                for r in results
            ],
        }

    @app.get("/api/runbooks/stats")
    def runbook_stats():
        """知识库统计 (用于首页 Dashboard)"""
        all_rbs = store.get_runbooks()
        return {
            "total": len(all_rbs),
            "approved": len([r for r in all_rbs if r["status"] == "approved"]),
            "pending_review": len([r for r in all_rbs if r["status"] == "pending_review"]),
            "rejected": len([r for r in all_rbs if r["status"] == "rejected"]),
            "manual": len([r for r in all_rbs if r["source"] == "manual"]),
            "agent_generated": len([r for r in all_rbs if r["source"] == "agent_generated"]),
        }

    @app.get("/api/runbooks/{rb_id}")
    def get_runbook(rb_id: str):
        """获取单个 runbook 详情"""
        rb = store.get_runbook(rb_id)
        if not rb:
            return JSONResponse(status_code=404, content={"detail": "runbook not found"})
        # 不返回 embedding (太大)
        rb.pop("embedding", None)
        return rb

    @app.post("/api/runbooks")
    def create_runbook(rb: dict):
        """新增 runbook (手动添加, 默认 approved)"""
        if not rb.get("title") or not rb.get("content"):
            return JSONResponse(status_code=400, content={"detail": "title and content required"})
        rb.setdefault("source", "manual")
        rb.setdefault("status", "approved")
        rb.setdefault("updated_by", "web-user")
        rid = store.upsert_runbook(rb)
        return {"id": rid, "status": "created"}

    @app.put("/api/runbooks/{rb_id}")
    def update_runbook(rb_id: str, rb: dict):
        """更新 runbook (内容修改后需重新编码 embedding)"""
        rb["id"] = rb_id
        rb.setdefault("updated_by", "web-user")
        # 如果 title/content/tags 变了, 清除 embedding 以便重新编码
        existing = store.get_runbook(rb_id)
        if existing:
            if (existing.get("title") != rb.get("title") or
                existing.get("content") != rb.get("content") or
                existing.get("tags") != rb.get("tags", existing.get("tags"))):
                # 清除旧 embedding (置 NULL, 非空 bytes), ensure_embeddings 会重新编码
                store.update_runbook_embedding(rb_id, None)
        store.upsert_runbook(rb)
        return {"id": rb_id, "status": "updated"}

    @app.delete("/api/runbooks/{rb_id}")
    def delete_runbook(rb_id: str):
        """删除 runbook"""
        store.delete_runbook(rb_id)
        return {"id": rb_id, "status": "deleted"}

    @app.post("/api/runbooks/{rb_id}/review")
    def review_runbook(rb_id: str, decision: dict):
        """审核 runbook: pending_review -> approved / rejected"""
        status = decision.get("status", "rejected")
        reviewer = decision.get("decided_by", "web-user")
        if status not in ("approved", "rejected"):
            return JSONResponse(status_code=400, content={"detail": "status must be approved or rejected"})
        store.review_runbook(rb_id, status, reviewer)
        # 审核通过后, 触发 embedding 编码 (异步, 不阻塞响应)
        if status == "approved":
            try:
                import importlib
                kb_module = importlib.import_module("src.kb")
                kb_module.ensure_embeddings(store)
            except Exception as e:
                # embedding 编码失败不影响审核, 首次 search_kb 时会重试
                pass
        return {"id": rb_id, "status": status, "reviewer": reviewer}

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
                except RuntimeError:
                    # 进程关闭时默认线程池已 shutdown, run_in_executor 无法再提交
                    # (cannot schedule new futures after shutdown) -> 干净退出
                    break
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return app
