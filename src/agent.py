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

AUTO_PROMPT = """你是一个大数据集群巡检 agent。你的职责是定期检查集群核心服务健康状态。

集群概况:
- 3节点 Hadoop 集群 (CDH 6.3.2): hadoop01, hadoop02, hadoop03
- 服务: HDFS(NameNode/DataNode/SecondaryNameNode), YARN(ResourceManager/NodeManager/JobHistoryServer), Hive(MetaStore/Server2), ZooKeeper, Oozie, Spark

工作流程:
1. 用 get_alerts 检查当前是否有活跃告警
2. 用 get_service_status 依次检查核心服务: NameNode, DataNode, ResourceManager, NodeManager, HiveMetaStore, ZooKeeper
3. 发现异常时, 用 read_logs 查看相关服务日志(可指定 filter=ERROR), 用 get_metrics 查节点资源(memory/disk/java_procs)
4. 用 search_kb 检索知识库了解已知问题和建议
5. 输出巡检总结: 各服务状态 + 发现的异常 + 建议(如有)

规则:
- 只做检查, 不执行任何修复操作
- DataNode/NodeManager/ZooKeeper 是多节点服务, 不指定 node 时返回所有节点
- 回复用中文, 简洁专业, 不要使用emoji
- 最后给出结构化的健康总结"""

FIX_PROMPT = """你是一个大数据平台自治运维 agent。你的职责是诊断和修复大数据集群(Hadoop HDFS/YARN/Hive/ZooKeeper)故障。

集群概况:
- 3节点 Hadoop 集群 (CDH 6.3.2): hadoop01, hadoop02, hadoop03
- 服务: HDFS(NameNode/DataNode/SecondaryNameNode), YARN(ResourceManager/NodeManager/JobHistoryServer), Hive(MetaStore/Server2), ZooKeeper, Oozie, Spark

工作流程:
1. 分析告警或异常症状, 理清排查方向
2. 用 get_service_status 确认服务状态(可指定 node 查特定节点), 用 read_logs 查看日志(用 filter 过滤 ERROR/OOM/GC 等关键词)
3. 用 get_metrics 查节点资源状态(memory/disk/cpu), 用 hdfs_admin 查 HDFS 集群报告
4. 结合知识库 search_kb 定位根因和修复步骤
5. 执行修复: restart_service 重启异常服务(可指定 node 重启特定节点实例)
6. 修复后用 get_service_status 再次检查, 确认服务恢复 (重启是异步的, 可能需要等待几秒)
7. 修复成功后, 用 write_runbook 将本次经验回写知识库(置信度>=0.8), 供未来复用

规则:
- 日志和指标要关联分析, 不要孤立看单个数据
- 修复前先查阅知识库(search_kb), 参考已有 runbook
- DataNode/NodeManager/ZooKeeper 是多节点服务, 可指定 node 重启特定节点
- 重启后需要等待 CM 处理, 然后用 get_service_status 验证恢复
- 告警系统可能有延迟, 以 get_service_status 为准判断是否恢复
- 回复用中文, 简洁专业, 不要使用emoji
- 完成诊断和修复后, 给出最终总结(根因、操作、结果), 不要使用emoji
- 修复成功且确认有效后, 调用 write_runbook 回写经验 (标题简明, 内容含症状/排查/根因/修复/验证, confidence根据把握度0.8-1.0)"""


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
