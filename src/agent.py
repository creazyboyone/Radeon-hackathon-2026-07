import json
import logging

try:
    from .web.event_bus import bus
except ImportError:
    bus = None

from .llm_client import LLMClient
from .tools import (TOOL_RISK, get_tool_definitions,
                   AUTO_TOOL_NAMES, FIX_TOOL_NAMES)
from .db import Store
from .guardrails import Guardrail
from .config import MAX_REACT_ITERATIONS, MAX_TOKENS, TEMPERATURE, AUTONOMY

logger = logging.getLogger(__name__)

AUTO_PROMPT = """你是一个大数据集群巡检 agent。你的职责是高效检查集群健康状态, 主动发现并上报异常。

集群概况:
- 3节点 Apache Hadoop 集群 (docker-compose): hadoop01, hadoop02, hadoop03
- 服务: HDFS(NameNode HA/DataNode/JournalNode), YARN(ResourceManager HA/NodeManager/JobHistoryServer), Hive(MetaStore/Server2), HBase(Master/RegionServer), ZooKeeper
- 监控: Prometheus + Grafana (JMX Exporter 采集各 daemon 指标)

可用工具: get_alerts, get_service_status, get_metrics, read_logs, search_kb, hdfs_admin, diagnose_node

巡检原则:
- 先看告警(get_alerts): 有告警则针对性排查, 无告警也不代表一切正常, 需主动抽查
- 巡检时必须抽查关键指标: 每次巡检至少调用一次 hdfs_admin(report) 和 get_metrics(disk)
- **分析工具输出**: 不要只看 overall_health 字段. 必须逐条分析工具返回的具体数值和文本, 识别异常信号. 你是集群健康的唯一判断者, 告警系统只做最基本的进程存活检测, 其余异常全靠你的分析. 常见异常信号包括但不限于:
  * 磁盘使用率 >= 85% (warning) 或 >= 90% (critical)
  * 内存可用 < 10%
  * hdfs_admin 输出中的 "Safe mode is ON", "Under replicated blocks" 异常增多, "Missing blocks" > 0, "Corrupt blocks" > 0
  * 日志中出现 ERROR/Exception/OOM/OutOfMemory/GC overhead/Timeout/Connection refused
  * 进程 uptime 异常短 (可能刚崩溃重启)
  * 任何不符合预期的数值或状态
- 灵活决策: 无需每次查所有服务所有指标, 但关键指标(disk/hdfs report)必须查
- 发现异常时: 针对性查日志和指标深入排查, 在巡检报告中明确标注异常项
- 无异常时简报即可, 有异常必须详细说明

规则:
- 只做检查, 不执行任何修复操作
- DataNode/NodeManager/ZooKeeper/RegionServer/JournalNode 是多节点服务, 不指定 node 时返回所有节点
- 回复用中文, 简洁专业, 不要使用emoji

输出格式 (严格遵守):
- 如果巡检发现任何异常, 你的回复必须以这行开头: ANOMALY_DETECTED
  第二行用一句话概括异常 (如: HDFS 存在 3 个坏块, 需要修复)
  然后是详细的巡检报告
- 如果巡检未发现异常, 你的回复以 HEALTHY 开头, 然后是简短的健康总结
- 这个标记会被调度器解析, 用于决定是否自动触发修复流程"""

FIX_PROMPT = """你是一个大数据平台自治运维 agent。你的职责是诊断和修复集群故障。

集群概况:
- 3节点 Apache Hadoop 集群 (docker-compose): hadoop01, hadoop02, hadoop03
- 服务: HDFS(NameNode HA/DataNode/JournalNode), YARN(ResourceManager HA/NodeManager/JobHistoryServer), Hive(MetaStore/Server2), HBase(Master/RegionServer), ZooKeeper
- 监控: Prometheus + Grafana (JMX Exporter 采集各 daemon 指标)

可用工具: get_alerts, get_service_status, get_metrics, read_logs, search_kb, hdfs_admin, restart_service, edit_remote_config, write_runbook, diagnose_node, file_ops

诊断原则:
- 精准定位: 根据告警信息针对性排查, 不要走固定流程
- **分析工具输出**: 不要只看 overall_health 字段. 必须逐条分析工具返回的具体数值和文本, 识别异常信号. 告警系统不可能覆盖所有问题, 你需要根据工具返回的数据自行判断什么是异常
- 最少调用: 用最少的工具调用定位根因, 避免不必要的检查
- 先查后修: 确认根因后再修复, 修复前可参考知识库(search_kb)已有经验
- 验证闭环: 修复后用 get_service_status 或对应工具验证恢复, 成功后回写runbook(write_runbook)

常见故障模式 (供参考, 不限于此):
- 进程停止/崩溃: 查状态+日志确认原因, restart_service 重启
- OOM: 查日志确认OOM关键词, 查内存指标, restart_service 重启
- 磁盘满: 查磁盘指标, 清理日志/临时文件
- GC过长: 查日志GC关键词, 调整GC参数
- 配置错误: 查日志报错, 对比配置, edit_remote_config 修正
- HDFS Safe Mode: hdfs_admin(report/safemode_get) 确认状态, 若为手动进入则 hdfs_admin(safemode_leave) 退出, 若为自动进入则检查 DataNode 是否下线导致块不足
- 磁盘使用率过高: get_metrics(disk) 确认使用率, 查找大文件或日志, 清理临时文件/日志释放空间, 确认服务恢复
- **未知故障**: 仔细分析日志和指标中的异常信号, 结合集群架构和服务依赖关系推理根因. 不要因为没有匹配的故障模式就放弃, 要主动分析并尝试修复
- diagnose_node 可用于任意诊断场景: du_root(磁盘占用)/find_large(大文件)/top_procs(进程)/netstat(端口)/custom(自定义只读命令)
- file_ops 可用于修复: delete(删除文件)/truncate(截断日志)/cleanup_logs(清理旧日志). 注意安全限制, 仅允许删除日志/临时文件
- 磁盘满修复流程: diagnose_node(du_root/find_large) 定位大文件 → file_ops(delete/cleanup_logs) 清理 → get_metrics(disk) 验证
- HDFS 坏块修复流程: hdfs_admin(fsck_list_corrupt) 列出坏块文件 → hdfs_admin(fsck_delete, path=/) 删除坏块文件 → hdfs_admin(report) 验证 Corrupt blocks=0

规则:
- DataNode/NodeManager/ZooKeeper/RegionServer/JournalNode 是多节点服务, 可指定 node 操作特定节点
- 重启后等待几秒再用 get_service_status 验证
- 回复用中文, 简洁专业, 不要使用emoji
- 修复成功后调用 write_runbook 回写经验 (标题简明, 内容含症状/根因/修复/验证, confidence 0.8-1.0)"""


class ReActAgent:
    def __init__(self, llm: LLMClient, store: Store, mode="fix",
                 guardrail: Guardrail = None):
        self.llm = llm
        self.store = store
        self.mode = mode
        self.guardrail = guardrail or Guardrail(store, autonomy=AUTONOMY)
        if mode == "auto":
            self.tool_names = AUTO_TOOL_NAMES
            self.system_prompt = AUTO_PROMPT
        else:
            self.tool_names = FIX_TOOL_NAMES
            self.system_prompt = FIX_PROMPT
        self.tool_defs = get_tool_definitions(self.tool_names)

    def run(self, user_message: str, parent_id=None, trigger="") -> str:
        sid = self.store.create_session(
            session_type=self.mode, parent_id=parent_id, trigger=trigger)
        seq = 0
        tag = f"[/{self.mode} {sid}]"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        self.store.log_event(sid, seq, "user_input", {"message": user_message}); seq += 1
        if bus:
            bus.publish({"type": "agent_event", "session_id": sid, "kind": "user_input", "content": {"message": user_message}})

        for i in range(MAX_REACT_ITERATIONS):
            logger.info(f"{tag} --- iteration {i+1}/{MAX_REACT_ITERATIONS} ---")

            # 流式输出: on_chunk 回调实时推送到 WebSocket
            def _on_chunk(chunk, _sid=sid):
                if bus:
                    kind = "stream_reasoning" if chunk["type"] == "reasoning" else "stream_content"
                    bus.publish({"type": "agent_event", "session_id": _sid, "kind": kind, "content": {"text": chunk["text"]}})

            try:
                resp = self.llm.chat_stream(
                    messages=messages, tools=self.tool_defs,
                    max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                    on_chunk=_on_chunk,
                )
            except Exception as e:
                logger.warning(f"{tag} chat_stream failed ({e}), fallback to chat")
                try:
                    resp = self.llm.chat(
                        messages=messages, tools=self.tool_defs,
                        max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                    )
                except Exception as e2:
                    logger.error(f"{tag} chat fallback also failed: {e2}")
                    self.store.log_event(sid, seq, "error", {"error": f"LLM unavailable: {e2}"}); seq += 1
                    self.store.finish_session(sid, summary=f"LLM error: {e2}", status="error")
                    return f"LLM error: {e2}"

            content = resp["content"]
            reasoning = resp["reasoning"]
            tool_calls = resp["tool_calls"]

            if reasoning:
                print(f"  {tag} [思考] {reasoning.strip()[:150]}")
                self.store.log_event(sid, seq, "reasoning", {"text": reasoning}); seq += 1
                if bus: bus.publish({"type": "agent_event", "session_id": sid, "kind": "reasoning", "content": {"text": reasoning}})

            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)
            self.store.log_event(sid, seq, "assistant", {"content": content, "tool_calls": tool_calls}); seq += 1
            if bus and (content or tool_calls):
                bus.publish({"type": "agent_event", "session_id": sid, "kind": "assistant", "content": {"text": content or "", "tool_calls": tool_calls}})

            if not tool_calls:
                print(f"  {tag} [完成] {content[:200]}")
                self.store.log_event(sid, seq, "final_answer", {"text": content}); seq += 1
                if bus:
                    bus.publish({"type": "agent_event", "session_id": sid, "kind": "final_answer", "content": {"text": content}})

                # M5 学习闭环: fix 模式下, 若修复成功但未调用 write_runbook, 追加一轮提示
                if self.mode == "fix" and not _session_used_tool(sid, self.store, "write_runbook"):
                    _maybe_prompt_runbook(self, sid, seq, messages, content, tag)
                    seq += 10  # 预留序号 (追加逻辑内部会自增)

                self.store.finish_session(sid, summary=content[:300])
                return content

            for tc in tool_calls:
                name = tc["name"]
                args = tc["arguments"]
                risk = TOOL_RISK.get(name, "low")
                print(f"  {tag} [工具] {name}({args}) risk={risk}")
                self.store.log_event(sid, seq, "tool_call", {"name": name, "args": args, "risk": risk}); seq += 1
                if bus: bus.publish({"type": "agent_event", "session_id": sid, "kind": "tool_call", "content": {"name": name, "args": args, "risk": risk}})

                result = self.guardrail.execute(name, args, session_id=sid)
                print(f"  {tag} [结果] {json.dumps(result, ensure_ascii=False)[:200]}")
                self.store.log_event(sid, seq, "tool_result", {"name": name, "result": result}); seq += 1
                if bus: bus.publish({"type": "agent_event", "session_id": sid, "kind": "tool_result", "content": {"name": name, "result": result}})

                messages.append({
                    "role": "tool", "tool_call_id": tc.get("id", ""),
                    "name": name, "content": json.dumps(result, ensure_ascii=False),
                })

        print(f"  {tag} [达到最大迭代数, 终止]")
        self.store.finish_session(sid, summary="max iterations", status="aborted")
        return "max iterations"


# ============================================================
# M5 学习闭环辅助函数
# ============================================================

def _session_used_tool(session_id: str, store: Store, tool_name: str) -> bool:
    """检查 session 是否已调用过指定工具"""
    try:
        with store.lock:
            row = store.conn.execute(
                "SELECT COUNT(*) FROM session_events "
                "WHERE session_id=? AND kind='tool_call' "
                "AND json_extract(content_json, '$.name')=?",
                (session_id, tool_name)
            ).fetchone()
        return row and row[0] > 0
    except Exception:
        return False


def _session_has_repair_action(session_id: str, store: Store) -> bool:
    """检查 session 是否执行过修复操作 (restart_service / edit_remote_config)"""
    try:
        with store.lock:
            row = store.conn.execute(
                "SELECT COUNT(*) FROM session_events "
                "WHERE session_id=? AND kind='tool_call' "
                "AND (json_extract(content_json, '$.name')='restart_service' "
                "     OR json_extract(content_json, '$.name')='edit_remote_config')",
                (session_id,)
            ).fetchone()
        return row and row[0] > 0
    except Exception:
        return False


def _maybe_prompt_runbook(agent, sid: str, seq: int, messages: list,
                           final_content: str, tag: str):
    """fix 模式修复成功后, 若未调用 write_runbook, 追加一轮提示让 agent 回写经验

    判断逻辑:
    - session 执行过 restart_service 或 edit_remote_config (确实修复了)
    - 最终回答中包含"成功/恢复/正常/OK/GOOD"等关键词 (修复有效)
    - 未调用过 write_runbook
    → 追加一条 user 消息, 提示 agent 调用 write_runbook
    """
    if not _session_has_repair_action(sid, agent.store):
        return

    # 检测修复成功关键词 (宽松匹配, 避免漏判)
    success_keywords = ["成功", "恢复", "正常", "已启动", "已修复", "GOOD", "RUNNING",
                        "已解决", "验证通过", "health"]
    content_lower = final_content.lower()
    is_success = any(kw.lower() in content_lower for kw in success_keywords)
    if not is_success:
        return

    # 追加提示
    prompt = (
        "检测到本次故障已成功修复。请调用 write_runbook 工具, 将本次排查和修复经验回写知识库, "
        "供未来遇到相同问题时快速复用。要求:\n"
        "- title: 简明描述故障场景 (如 'DataNode OOM 崩溃修复')\n"
        "- content: 结构化描述, 包含 症状/排查步骤/根因/修复方法/验证方式\n"
        "- tags: 相关标签 (逗号分隔, 如 hdfs,datanode,oom)\n"
        "- confidence: 0.8-1.0 (根据修复把握度)"
    )
    messages.append({"role": "user", "content": prompt})
    agent.store.log_event(sid, seq, "runbook_prompt", {"text": prompt})
    if bus:
        bus.publish({"type": "agent_event", "session_id": sid,
                     "kind": "runbook_prompt", "content": {"text": prompt}})

    # 再跑一轮 LLM 让它调用 write_runbook
    try:
        resp = agent.llm.chat(
            messages=messages, tools=agent.tool_defs,
            max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
        )
        tool_calls = resp.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                name = tc["name"]
                if name == "write_runbook":
                    args = tc["arguments"]
                    print(f"  {tag} [学习闭环] 自动回写 runbook: {args.get('title', '')}")
                    result = agent.guardrail.execute(name, args, session_id=sid)
                    agent.store.log_event(sid, seq + 1, "tool_call",
                                          {"name": name, "args": args, "auto": True})
                    agent.store.log_event(sid, seq + 2, "tool_result",
                                          {"name": name, "result": result})
                    if bus:
                        bus.publish({"type": "agent_event", "session_id": sid,
                                     "kind": "tool_call",
                                     "content": {"name": name, "args": args, "auto": True}})
                        bus.publish({"type": "agent_event", "session_id": sid,
                                     "kind": "tool_result",
                                     "content": {"name": name, "result": result}})
                    logger.info(f"{tag} 学习闭环: runbook 已回写 (id={result.get('id', '')})")
    except Exception as e:
        logger.warning(f"{tag} 自动回写 runbook 失败: {e}")
