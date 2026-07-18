import logging
import signal
import sys
import threading

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from src.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, DB_PATH
from src.llm_client import LLMClient
from src.db import Store
from src.orchestrator import Orchestrator
from src.tools import set_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def run_web(store):
    """子线程运行 FastAPI web 控制台"""
    import uvicorn
    from src.web.app import create_app
    app = create_app(store)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info",
                ws="wsproto")


def main():
    logger.info(f"LLM: {LLM_BASE_URL} model={LLM_MODEL}")
    logger.info(f"DB: {DB_PATH}")

    llm = LLMClient(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
    store = Store(DB_PATH)

    # M5: 注入 store 到 tools 模块, 供 search_kb / write_runbook 访问知识库
    set_store(store)

    # 启动 web 控制台 (子线程)
    web_thread = threading.Thread(
        target=run_web, args=(store,), daemon=True)
    web_thread.start()
    logger.info("Web console: http://localhost:8000")
    logger.info("  API: /api/sessions /api/approvals /api/audit")
    logger.info("  WS:  /ws (agent events)")

    # 主线程: Orchestrator 循环巡检
    orch = Orchestrator(
        llm, store,
        inspect_interval=15,
    )

    # 优雅关闭: 收到信号时标记 master session 为 done
    def _shutdown(signum, frame):
        logger.info(f"收到信号 {signum}, 正在关闭...")
        if orch.master_sid:
            store.finish_session(orch.master_sid, summary="shutdown", status="done")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    orch.run(max_cycles=100)

    # 打印 session 树
    logger.info("Session 树:")
    with store.lock:
        rows = store.conn.execute(
            "SELECT id, parent_id, type, status FROM sessions ORDER BY started_at"
        ).fetchall()
    for row in rows:
        prefix = "  └─" if row[1] else "  ├─"
        print(f"{prefix} [{row[2]}] {row[0]} status={row[3]} parent={row[1] or '-'}")


if __name__ == "__main__":
    main()
