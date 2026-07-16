import json
import time
import logging

from .llm_client import LLMClient
from .db import Store
from .agent import ReActAgent
from .tools import get_pending_alerts, get_cluster_snapshot

logger = logging.getLogger(__name__)


class Orchestrator:
    """master 调度器 - 纯规则, 不调 LLM。管理 /auto 巡检 + /fix 抢占。

    故障注入由用户手动操作（kill 进程 / CM 停服务）。
    Agent 循环巡检, 发现异常自动触发 /fix。
    """

    def __init__(self, llm: LLMClient, store: Store,
                 inspect_interval=15, auto_fault_inject=False, fault_delay=20):
        self.llm = llm
        self.store = store
        self.inspect_interval = inspect_interval
        self.auto_fault_inject = auto_fault_inject
        self.fault_delay = fault_delay
        self.last_inspect = 0
        self.master_sid = None
        self.fix_in_progress = False
        self.start_time = time.time()
        # CM API 角色状态更新有延迟, 重启后等几秒再验证
        self.post_fix_delay = 5

    def run(self, max_cycles=100):
        # 关闭旧的 running master session (避免多个 master 并存)
        self.store.conn.execute(
            "UPDATE sessions SET status='done', ended_at=? "
            "WHERE type='master' AND status='running'",
            (int(time.time()),)
        )
        self.store.conn.commit()
        self.master_sid = self.store.create_session(
            session_type="master", trigger="orchestrator")
        print(f"\n{'='*60}")
        print(f"  Orchestrator 启动 (master session={self.master_sid})")
        print(f"  巡检间隔={self.inspect_interval}s")
        print(f"  集群后端: CDH CM API @ {self._cm_url()}")
        print(f"  模式: 循环巡检, 等待用户手动注入故障")
        print(f"  >>> 你可以随时停掉某台节点的服务, agent 会自动检测并尝试修复 <<<")
        print(f"{'='*60}\n")

        cycle = 0
        while cycle < max_cycles:
            cycle += 1
            elapsed = int(time.time() - self.start_time)

            # 检查告警 -> 抢占
            alerts = get_pending_alerts()
            if alerts:
                alert = alerts[0]
                print(f"\n>>> [T+{elapsed}s] 检测到告警: {alert['alertname']} "
                      f"severity={alert['severity']}")
                if alert.get('node'):
                    print(f">>> 节点: {alert['node']}, 状态: {alert.get('roleState','')}")
                print(f">>> 启动 /fix (抢占巡检) <<<\n")
                self._run_fix(alert, elapsed)
                # fix 完成后等待 CM 更新状态
                print(f">>> 等待 {self.post_fix_delay}s 让状态更新...")
                time.sleep(self.post_fix_delay)
                # fix 完成后强制立即巡检验证
                self.last_inspect = 0
                continue

            # 到点巡检
            if time.time() - self.last_inspect >= self.inspect_interval:
                print(f"\n>>> [T+{elapsed}s] 定时巡检 -> 启动 /auto <<<\n")
                self._run_auto(elapsed)
                self.last_inspect = time.time()
                continue

            time.sleep(2)

        print(f"\n{'='*60}")
        print(f"  Orchestrator 结束 (共 {cycle} 轮)")
        print(f"{'='*60}")

    def _run_auto(self, elapsed):
        """巡检子session - 一次性 context, 只读工具"""
        state_card = self.store.get_latest_state_card()
        prompt = "执行例行集群巡检。"
        if state_card:
            prompt += f"\n上次巡检状态卡: {json.dumps(state_card, ensure_ascii=False)}"

        agent = ReActAgent(self.llm, self.store, mode="auto")
        result = agent.run(prompt, parent_id=self.master_sid, trigger="cron")

        # 保存状态卡 (结构化快照 + 巡检摘要)
        snapshot_after = get_cluster_snapshot()
        self.store.save_state_card({
            "inspect_time": f"T+{elapsed}s",
            "cluster": snapshot_after,
            "summary": result[:200],
        })
        print(f"\n>>> [T+{elapsed}s] 巡检完成, 状态卡已保存 <<<\n")

    def _run_fix(self, alert, elapsed):
        """修复子session - 一次性 context, 全工具(含高危)"""
        node_info = alert.get('node', '')
        state_info = alert.get('roleState', '') or alert.get('healthSummary', '')
        prompt = (f"告警: {alert['alertname']} on {node_info} "
                  f"(severity={alert['severity']}")
        if state_info:
            prompt += f", 状态={state_info}"
        prompt += f")\n摘要: {alert.get('summary','')}\n请诊断并修复此故障。"

        agent = ReActAgent(self.llm, self.store, mode="fix")
        result = agent.run(prompt, parent_id=self.master_sid, trigger=f"alert:{alert['alertname']}")
        print(f"\n>>> [T+{elapsed}s] 修复完成 <<<\n")

    @staticmethod
    def _cm_url():
        from .config import CM_HOST, CM_PORT, CM_CLUSTER
        return f"http://{CM_HOST}:{CM_PORT} cluster={CM_CLUSTER}"
