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

    两层故障发现机制:
    1. 快速路径: get_pending_alerts() 检测进程存活 (Prometheus target down / supervisor STOPPED)
       → 立即触发 /fix, 无需等巡检周期
    2. 巡检升级: /auto session 中 LLM 分析工具输出 (hdfs report / metrics / logs)
       → 发现异常时输出 ANOMALY_DETECTED 标记 → 自动触发 /fix
       → 覆盖告警系统无法预编码的故障 (Safe Mode / 磁盘满 / 坏块 / OOM / 未知故障)

    故障注入由用户手动操作（kill 进程 / 篡改数据 / 填满磁盘等）。
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
        self.start_time = time.time()
        # 重启后等几秒再验证 (supervisorctl/CM API 状态更新有延迟)
        self.post_fix_delay = 5
        # 停止标志: 信号 handler 设置后, 巡检循环自然退出 (优雅关闭)
        self._stop = False

    def stop(self):
        """请求常驻循环优雅停止 (由信号 handler 调用)。"""
        self._stop = True

    def run(self, max_cycles=None):
        """常驻巡检循环。max_cycles=None 表示无限运行 (24h 无人值守),
        传入正整数仅用于测试/演示限制轮数。收到信号调用 stop() 后优雅退出。"""
        self._stop = False  # 重置 (支持 run 被多次调用)
        # 关闭旧的 running master session (避免多个 master 并存)
        with self.store.lock:
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
        print(f"  集群后端: {self._backend_info()}")
        print(f"  模式: 循环巡检, 等待用户手动注入故障")
        print(f"  告警系统: 仅进程存活检测 (快速路径)")
        print(f"  巡检升级: LLM 发现异常 → 自动触发 /fix")
        print(f"  >>> 你可以随时停掉服务/注入故障, agent 会自动检测并尝试修复 <<<")
        print(f"{'='*60}\n")

        cycle = 0
        while not self._stop and (max_cycles is None or cycle < max_cycles):
            cycle += 1
            elapsed = int(time.time() - self.start_time)

            try:
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
                    # fix 完成后等待状态更新 (supervisorctl/CM API)
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
            except Exception as e:
                # 单轮异常 (LLM 断连 / CM API 超时等) 不终止常驻循环, 记录后退避重试
                logger.exception(f"[T+{elapsed}s] 调度轮次异常, 跳过本轮: {e}")
                time.sleep(5)
                continue

            time.sleep(2)

        # 收尾: master session 标记为 done (供 Web 回看)
        if self.master_sid:
            self.store.finish_session(self.master_sid, summary="stopped", status="done")
        reason = "收到停止信号" if self._stop else "达到最大轮数"
        print(f"\n{'='*60}")
        print(f"  Orchestrator 停止 ({reason}, 共 {cycle} 轮)")
        print(f"{'='*60}")

    def _run_auto(self, elapsed):
        """巡检子session - 一次性 context, 只读工具.
        
        如果巡检 LLM 发现异常 (回复以 ANOMALY_DETECTED 开头),
        自动触发 /fix 修复流程, 无需预编码告警规则.
        """
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

        # 巡检升级: LLM 发现异常 → 自动触发 /fix
        if result and result.strip().startswith("ANOMALY_DETECTED"):
            lines = result.strip().split("\n", 2)
            anomaly_brief = lines[1].strip() if len(lines) > 1 else "巡检发现异常"
            anomaly_detail = lines[2].strip() if len(lines) > 2 else result[:500]
            print(f"\n>>> [T+{elapsed}s] 巡检发现异常: {anomaly_brief}")
            print(f">>> 自动升级为 /fix 修复流程 <<<\n")

            # 构造虚拟告警, 复用 _run_fix 流程
            synthetic_alert = {
                "alertname": "INSPECTION_ANOMALY",
                "severity": "critical",
                "summary": anomaly_brief,
                "detail": anomaly_detail,
            }
            self._run_fix(synthetic_alert, elapsed)
            # fix 完成后等待状态更新
            print(f">>> 等待 {self.post_fix_delay}s 让状态更新...")
            time.sleep(self.post_fix_delay)
            self.last_inspect = 0  # 强制下一轮立即巡检验证

    def _run_fix(self, alert, elapsed):
        """修复子session - 一次性 context, 全工具(含高危)"""
        node_info = alert.get('node', '')
        state_info = alert.get('roleState', '') or alert.get('healthSummary', '')
        prompt = (f"告警: {alert['alertname']} on {node_info} "
                  f"(severity={alert['severity']}")
        if state_info:
            prompt += f", 状态={state_info}"
        prompt += f")\n摘要: {alert.get('summary','')}"
        # 巡检升级的告警携带详细分析上下文
        if alert.get('detail'):
            prompt += f"\n巡检分析: {alert['detail']}"
        prompt += "\n请诊断并修复此故障。"

        agent = ReActAgent(self.llm, self.store, mode="fix")
        result = agent.run(prompt, parent_id=self.master_sid, trigger=f"alert:{alert['alertname']}")
        print(f"\n>>> [T+{elapsed}s] 修复完成 <<<\n")

    @staticmethod
    def _backend_info():
        from .config import CLUSTER_BACKEND, PROMETHEUS_URL, CM_HOST, CM_PORT, CM_CLUSTER
        if CLUSTER_BACKEND == "cdh":
            return f"CDH CM API @ http://{CM_HOST}:{CM_PORT} cluster={CM_CLUSTER}"
        else:
            return f"Apache Hadoop (docker-compose) + Prometheus @ {PROMETHEUS_URL}"
