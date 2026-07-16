import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from src.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, DB_PATH
from src.llm_client import LLMClient
from src.db import Store
from src.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    logger.info(f"LLM: {LLM_BASE_URL} model={LLM_MODEL}")
    logger.info(f"DB: {DB_PATH}")

    llm = LLMClient(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
    store = Store(DB_PATH)

    # Orchestrator: 循环巡检, 等待用户手动注入故障
    orch = Orchestrator(
        llm, store,
        inspect_interval=15,      # 每 15s 巡检一次
    )
    orch.run(max_cycles=100)

    # 打印 session 树
    logger.info("Session 树:")
    for row in store.conn.execute(
        "SELECT id, parent_id, type, status FROM sessions ORDER BY started_at"
    ):
        prefix = "  └─" if row[1] else "  ├─"
        print(f"{prefix} [{row[2]}] {row[0]} status={row[3]} parent={row[1] or '-'}")


if __name__ == "__main__":
    main()
