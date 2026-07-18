"""
工具层 — 真实集群操作 (SSH + CM API)

配置驱动, 切换环境只改 config.py:
  - CLUSTER_BACKEND="cdh"  -> Cloudera Manager API + SSH
  - CLUSTER_BACKEND="apache" -> docker-compose + SSH (待搭建)

所有 IP/节点/日志路径/用户均从 config.SERVICE_MAP 读取, 不硬编码。
"""
import json
import logging
import shlex
import subprocess
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    CLUSTER_BACKEND, SSH_USER, SSH_PORT, SSH_OPTS,
    CLUSTER_NODES, SERVICE_MAP, INSPECT_SERVICES,
    CM_HOST, CM_PORT, CM_USER, CM_PASS, CM_CLUSTER, CM_API_VERSION,
    JPS_BIN, HADOOP_SBIN, YARN_SBIN,
)

logger = logging.getLogger(__name__)

# ---- 风险分级 ----
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_DESTRUCTIVE = "destructive"

# ---- 全局 Store 引用 (M5: search_kb / write_runbook 需要访问 DB) ----
# 由 main.py / orchestrator.py 启动时注入, 工具函数通过 _get_store() 获取
_store_ref = None


def set_store(store):
    """注入 Store 实例 (启动时调用一次)"""
    global _store_ref
    _store_ref = store


def _get_store():
    """获取已注入的 Store 实例"""
    return _store_ref

# ---- 工具定义 (给 LLM 的 function schema) ----
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "获取大数据集群中某个服务的运行状态(含所有节点实例). 可选指定节点查看特定实例",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "服务名: NameNode/DataNode/ResourceManager/NodeManager/HiveMetaStore/HiveServer2/ZooKeeper/Oozie/SecondaryNameNode/JobHistoryServer"},
                    "node": {"type": "string", "description": "节点名(可选), 如 hadoop03. 不指定则返回所有节点"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "获取当前集群所有活跃告警(健康检查非GOOD的服务和角色)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "查询某个节点的系统指标(内存/磁盘/CPU/负载/Java进程)",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "指标类型: memory(内存)/disk(磁盘)/cpu(CPU)/load(负载)/java_procs(Java进程)"},
                    "node": {"type": "string", "description": "节点名, 如 hadoop03. 不指定则查所有节点"},
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_logs",
            "description": "读取服务日志(返回压缩摘要, 非原始全文). 可按关键词过滤, 可指定节点. 对多节点服务(如DataNode)默认读所有节点",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "服务名"},
                    "filter": {"type": "string", "description": "过滤关键词, 如 ERROR/OOM/GC/SIGTERM"},
                    "tail_n": {"type": "integer", "description": "每节点读取最后N行, 默认50"},
                    "node": {"type": "string", "description": "节点名(可选), 如 hadoop03. 不指定则读所有节点"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "检索运维知识库(runbook/调优经验), 返回相关条目. 暂用关键词匹配, 后续接入sqlite-vec向量检索",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "重启大数据服务(高危操作). 需提供理由. 核心服务(NameNode/ResourceManager)判高危, 非核心服务(DataNode/NodeManager)可自动执行. 注意: CM API 以服务为单位启动所有已停止的角色, 指定 node 仅用于筛选检查范围, 不支持精确重启单节点",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "服务名"},
                    "reason": {"type": "string", "description": "重启理由"},
                    "node": {"type": "string", "description": "节点名(可选, 多节点服务可指定单节点重启)"},
                },
                "required": ["service", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hdfs_admin",
            "description": "执行HDFS管理命令(只读): dfsadmin -report / fsck / dfs -ls / dfs -du",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作: report(集群报告)/fsck(文件系统检查)/ls(列目录)/du(目录大小)"},
                    "path": {"type": "string", "description": "HDFS路径, ls/du时必填"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_remote_config",
            "description": "修改远程节点配置文件(reversible档). 先备份 .bak.<ts>, 再 sed 替换, 最后 reload 服务. 用于调整 JVM heap/参数等可回滚操作",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "服务名(用于 reload): NameNode/DataNode/ResourceManager 等"},
                    "node": {"type": "string", "description": "节点名, 如 hadoop03"},
                    "file": {"type": "string", "description": "远程配置文件完整路径"},
                    "find": {"type": "string", "description": "要查找的原始内容(将被替换)"},
                    "replace": {"type": "string", "description": "替换为的新内容"},
                    "reason": {"type": "string", "description": "修改理由"},
                },
                "required": ["service", "node", "file", "find", "replace", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_runbook",
            "description": "解决故障后将经验回写为运维知识库runbook(学习闭环). 高置信度的成功修复自动写入, 待人工审核后生效. 避免重复排查相同问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "runbook标题, 简明描述故障场景, 如 'DataNode OOM 崩溃修复'"},
                    "content": {"type": "string", "description": "runbook内容: 包含症状/排查步骤/根因/修复方法/验证方式, 用结构化文本描述"},
                    "tags": {"type": "string", "description": "标签(逗号分隔), 如 'hdfs,datanode,oom', 便于检索分类"},
                    "confidence": {"type": "number", "description": "置信度0-1, 1=非常确定修复有效, 0.5=不确定. 低于0.7会被拒绝写入"},
                },
                "required": ["title", "content", "confidence"],
            },
        },
    },
]

# ---- 风险映射 (fallback, classify 优先用 DB risk_rules) ----
TOOL_RISK = {
    "get_service_status": RISK_LOW,
    "get_alerts": RISK_LOW,
    "get_metrics": RISK_LOW,
    "read_logs": RISK_LOW,
    "search_kb": RISK_LOW,
    "restart_service": RISK_HIGH,
    "hdfs_admin": RISK_LOW,
    "edit_remote_config": RISK_MEDIUM,
    "write_runbook": RISK_LOW,   # M5: 只写DB, 不影响集群, 低危
}

TOOL_HANDLERS = {}


def tool(name):
    def deco(fn):
        TOOL_HANDLERS[name] = fn
        return fn
    return deco


# ============================================================
# 底层: SSH 执行 + CM API 调用
# ============================================================

def ssh_exec(host_ip, command, timeout=30):
    """在远程节点上执行 SSH 命令, 返回 (stdout, stderr, returncode)"""
    cmd = ["ssh"] + SSH_OPTS.split() + [
        "-p", str(SSH_PORT),
        f"{SSH_USER}@{host_ip}",
        command,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, encoding="utf-8")
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "SSH timeout", -1
    except Exception as e:
        return "", f"SSH error: {e}", -1


# CM API session: 禁用重试 + 缩短超时, 避免 CM 不可达时拖死主循环
_cm_session = None


def _get_cm_session():
    global _cm_session
    if _cm_session is None:
        _cm_session = requests.Session()
        _cm_session.mount("http://", HTTPAdapter(max_retries=Retry(total=0)))
        _cm_session.mount("https://", HTTPAdapter(max_retries=Retry(total=0)))
    return _cm_session


def cm_get(path):
    """CM API GET 请求"""
    url = f"http://{CM_HOST}:{CM_PORT}/api/{CM_API_VERSION}{path}"
    try:
        resp = _get_cm_session().get(url, auth=(CM_USER, CM_PASS), timeout=(3, 5))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"CM API GET {path} failed: {e}")
        return {}


def cm_post(path):
    """CM API POST 请求 (重启/停止等)"""
    url = f"http://{CM_HOST}:{CM_PORT}/api/{CM_API_VERSION}{path}"
    try:
        resp = _get_cm_session().post(url, auth=(CM_USER, CM_PASS), timeout=(3, 5))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"CM API POST {path} failed: {e}")
        return {}


def _node_ip(node_key):
    """节点名 -> IP"""
    node = CLUSTER_NODES.get(node_key)
    if node:
        return node["host"]
    # 也允许直接传 IP
    return node_key


def _node_hostname(node_key):
    """节点名 -> hostname"""
    node = CLUSTER_NODES.get(node_key)
    if node:
        return node["hostname"]
    return node_key


# hostId -> hostname 缓存 (CM API roles 返回 hostId 而非 hostname)
_host_map_cache = None


def _build_host_map():
    """通过全局 /hosts API 获取 hostId -> hostname 映射"""
    global _host_map_cache
    if _host_map_cache is not None:
        return _host_map_cache
    data = cm_get("/hosts")
    _host_map_cache = {
        h.get("hostId", ""): h.get("hostname", "")
        for h in data.get("items", [])
    }
    return _host_map_cache


def _resolve_hostname(host_ref):
    """从 role 的 hostRef 解析出 hostname"""
    host_id = host_ref.get("hostId", "")
    if host_id:
        return _build_host_map().get(host_id, host_id)
    return host_ref.get("hostname", "")


def _find_role_name(cm_service, role_type, hostname):
    """通过 CM API 找到角色的真实名称 (roleName), 用 hostId 映射匹配 hostname"""
    data = cm_get(f"/clusters/{CM_CLUSTER}/services/{cm_service}/roles")
    for role in data.get("items", []):
        if role.get("type") == role_type:
            actual_hostname = _resolve_hostname(role.get("hostRef", {}))
            if actual_hostname == hostname:
                return role.get("name")
    return None


def _resolve_node(service_info, node):
    """解析目标节点列表. node=None -> 所有节点; node=hadoop03 -> [hadoop03]"""
    if node:
        return [node]
    return service_info.get("nodes", [])


def _log_filename(service_info, hostname):
    """构造日志文件名: hadoop-cmf-hdfs-DATANODE-hadoop03.yuf.com.log.out"""
    return f"{service_info['log_prefix']}-{hostname}.log.out"


# ============================================================
# 工具实现
# ============================================================

@tool("get_service_status")
def _get_service_status(service="", node=""):
    """通过 CM API 获取服务各角色状态"""
    svc_info = SERVICE_MAP.get(service)
    if not svc_info:
        return {"error": f"未知服务: {service}",
                "available": list(SERVICE_MAP.keys())}

    cm_svc = svc_info["cm_service"]
    data = cm_get(f"/clusters/{CM_CLUSTER}/services/{cm_svc}/roles")
    roles = data.get("items", [])

    # 筛选目标角色类型
    target_type = svc_info["cm_role_type"]
    target_nodes = _resolve_node(svc_info, node)
    target_hostnames = {_node_hostname(n) for n in target_nodes}

    role_list = []
    for r in roles:
        if r.get("type") != target_type:
            continue
        hostname = _resolve_hostname(r.get("hostRef", {}))
        if target_hostnames and hostname not in target_hostnames:
            continue
        role_list.append({
            "name": r.get("name", ""),
            "node": hostname,
            "roleState": r.get("roleState", ""),
            "healthSummary": r.get("healthSummary", ""),
            "healthChecks": [
                {"name": hc.get("name", ""), "summary": hc.get("summary", "")}
                for hc in r.get("healthChecks", [])
                if hc.get("summary") != "GOOD"
            ],
        })

    # 汇总健康状态
    bad_roles = [r for r in role_list if r["healthSummary"] not in ("GOOD", "DISABLED")]
    overall = "BAD" if bad_roles else "GOOD"

    return {
        "service": service,
        "overall_health": overall,
        "role_count": len(role_list),
        "roles": role_list,
    }


@tool("get_alerts")
def _get_alerts():
    """遍历所有 CM 服务, 收集非 GOOD 的健康检查作为告警"""
    services_data = cm_get(f"/clusters/{CM_CLUSTER}/services")
    alerts = []
    for svc in services_data.get("items", []):
        svc_name = svc.get("name", "")
        svc_health = svc.get("healthSummary", "")
        svc_type = svc.get("type", "")
        if svc_health != "GOOD":
            alerts.append({
                "alertname": f"{svc_type}_UNHEALTHY",
                "severity": "critical" if svc_health == "BAD" else "warning",
                "service": svc_name,
                "summary": f"Service {svc_name} health={svc_health}",
            })
        # 检查各角色
        role_data = cm_get(f"/clusters/{CM_CLUSTER}/services/{svc_name}/roles")
        for role in role_data.get("items", []):
            r_health = role.get("healthSummary", "")
            r_state = role.get("roleState", "")
            hostname = _resolve_hostname(role.get("hostRef", {}))
            # CDH 正常状态: STARTED, ACTIVE, ENABLED, NA, DISABLED
            # 异常状态: STOPPED, UNKNOWN, DOWN
            _NORMAL_STATES = ("STARTED", "ACTIVE", "ENABLED", "NA", "DISABLED")
            if r_health == "BAD" or r_state not in _NORMAL_STATES:
                alerts.append({
                    "alertname": f"{role.get('type', 'UNKNOWN')}_DOWN",
                    "severity": "critical" if r_state == "STOPPED" else "warning",
                    "node": hostname,
                    "roleState": r_state,
                    "healthSummary": r_health,
                    "summary": f"{role.get('type', '')} on {hostname}: "
                               f"state={r_state} health={r_health}",
                })

    return {"alerts": alerts, "count": len(alerts)}


@tool("get_metrics")
def _get_metrics(metric="", node=""):
    """通过 SSH 执行系统命令获取节点指标"""
    # 解析目标节点
    if node:
        nodes = [node]
    else:
        nodes = list(CLUSTER_NODES.keys())

    # 命令模板
    metric_cmds = {
        "memory": "free -m | head -3",
        "disk": "df -h / | tail -1",
        "cpu": "top -bn1 | head -5",
        "load": "cat /proc/loadavg",
        "java_procs": f"{JPS_BIN} 2>/dev/null || ps aux | grep java | grep -oP '(?<=Dproc_)\\w+' | sort -u",
    }
    cmd = metric_cmds.get(metric)
    if not cmd:
        return {"error": f"未知指标: {metric}", "available": list(metric_cmds.keys())}

    results = {}
    for n in nodes:
        ip = _node_ip(n)
        stdout, stderr, rc = ssh_exec(ip, cmd, timeout=30)
        results[n] = {
            "hostname": _node_hostname(n),
            "output": stdout if stdout else stderr,
            "returncode": rc,
        }
    return {"metric": metric, "nodes": results}


@tool("read_logs")
def _read_logs(service="", filter="", tail_n=50, node=""):
    """通过 SSH 读取服务日志, 预压缩: 只返回匹配 filter 的行(无 filter 返回最后 N 行)"""
    svc_info = SERVICE_MAP.get(service)
    if not svc_info:
        return {"error": f"未知服务: {service}",
                "available": list(SERVICE_MAP.keys())}

    target_nodes = _resolve_node(svc_info, node)
    log_dir = svc_info["log_dir"]
    all_results = []

    for n in target_nodes:
        ip = _node_ip(n)
        hostname = _node_hostname(n)
        logfile = _log_filename(svc_info, hostname)
        filepath = f"{log_dir}/{logfile}"

        # 安全: 转义用户输入防止命令注入
        try:
            tail_n = int(tail_n)
        except (TypeError, ValueError):
            tail_n = 50
        safe_filepath = shlex.quote(filepath)
        if filter:
            safe_filter = shlex.quote(filter)
            cmd = f"grep -i {safe_filter} {safe_filepath} 2>/dev/null | tail -{tail_n}"
        else:
            cmd = f"tail -{tail_n} {safe_filepath} 2>/dev/null"

        stdout, stderr, rc = ssh_exec(ip, cmd, timeout=30)
        lines = stdout.split("\n") if stdout else []

        # 预压缩: 统计 ERROR/FATAL/WARN 行数
        errors = [l for l in lines if "ERROR" in l or "FATAL" in l]
        warns = [l for l in lines if "WARN" in l]

        all_results.append({
            "node": hostname,
            "log_file": filepath,
            "total_lines": len(lines),
            "error_count": len(errors),
            "warn_count": len(warns),
            "errors": errors[:5],       # 最多5条错误
            "sample": lines[:10],        # 最多10行采样
        })

    # 汇总
    total_errors = sum(r["error_count"] for r in all_results)
    return {
        "service": service,
        "filter": filter or "none",
        "nodes_checked": len(all_results),
        "total_errors": total_errors,
        "results": all_results,
    }


@tool("search_kb")
def _search_kb(query=""):
    """检索运维知识库 — M5: 向量检索(bge-small) + BM25(FTS5) 混合检索

    优先使用语义向量检索 (bge-small-zh CPU), 降级为 BM25 关键词匹配。
    返回 approved 状态的 runbook, 含标题/内容/标签/匹配分数。
    """
    store = _get_store()
    if store is None:
        # store 未注入 (如单元测试), 退回静态 mock 数据
        return _search_kb_static(query)

    # 懒加载: 首次调用时确保所有 runbook 有 embedding
    try:
        from . import kb
        kb.ensure_embeddings(store)
        results = kb.hybrid_search(store, query, limit=5)
    except Exception as e:
        logger.warning(f"search_kb hybrid search failed ({e}), fallback to FTS")
        results = store.search_runbooks_fts(query, limit=5)

    if not results:
        return {
            "query": query,
            "matches": 0,
            "results": [],
            "message": "知识库中未找到相关条目",
        }

    # 精简返回 (content 截断, 避免占用过多 context)
    simplified = []
    for r in results:
        content = r.get("content", "")
        simplified.append({
            "title": r["title"],
            "content": content[:500] + ("..." if len(content) > 500 else ""),
            "tags": r.get("tags", ""),
            "score": r.get("score", 0),
            "match_type": r.get("match_type", ""),
        })

    return {
        "query": query,
        "matches": len(simplified),
        "results": simplified,
        "search_mode": "hybrid" if any(r.get("match_type") == "vector" for r in results) else "bm25",
    }


def _search_kb_static(query=""):
    """静态 mock 数据 (store 未注入时的兜底, 仅用于单元测试)"""
    _kb = [
        {"title": "DataNode OOM 修复runbook",
         "content": "DataNode OOM 崩溃修复步骤: 1.检查DataNode日志确认OOM "
                    "2.检查HADOOP_HEAPSIZE配置 3.调大heap至8192MB "
                    "4.重启DataNode: 通过CM API或systemctl restart "
                    "5.验证: jps确认DataNode进程存在, hdfs dfsadmin -report确认Live Datanodes"},
        {"title": "NameNode GC overhead 排查",
         "content": "NameNode GC overhead 原因: 1.堆内存不足 2.小文件过多 3.GC策略不当. "
                    "排查: jstat -gcutil 查看GC频率, 检查hdfs count看文件数. "
                    "修复: 调大NameNode堆内存, 启用G1GC, 清理小文件"},
    ]
    results = [kb for kb in _kb
               if any(w in kb["title"] or w in kb["content"]
                      for w in query.split())]
    return {"query": query, "matches": len(results), "results": results,
            "search_mode": "static_fallback"}


@tool("write_runbook")
def _write_runbook(title="", content="", tags="", confidence=1.0, session_id=""):
    """M5 学习闭环 — 将修复经验回写为知识库 runbook

    置信度门控: confidence < 0.7 拒绝写入 (防止错误经验污染知识库)
    agent 回写的 runbook 状态为 pending_review, 需人工审核后生效
    """
    store = _get_store()
    if store is None:
        return {"error": "知识库未初始化 (store 未注入)"}

    # 置信度门控 (§14.2: 防止错误经验被记住污染 KB)
    CONFIDENCE_THRESHOLD = 0.7
    if confidence < CONFIDENCE_THRESHOLD:
        return {
            "error": f"置信度 {confidence} 低于阈值 {CONFIDENCE_THRESHOLD}, 拒绝写入",
            "rejected": True,
            "message": "修复置信度不足, 建议人工确认后手动添加",
        }

    # 参数校验
    if not title.strip() or not content.strip():
        return {"error": "title 和 content 不能为空"}

    # 写入 (source=agent_generated, status=pending_review)
    rb_id = store.upsert_runbook({
        "title": title,
        "content": content,
        "tags": tags,
        "source": "agent_generated",
        "status": "pending_review",
        "session_id": session_id,
        "confidence": confidence,
        "updated_by": f"agent:{session_id}" if session_id else "agent",
    })

    return {
        "id": rb_id,
        "title": title,
        "status": "pending_review",
        "confidence": confidence,
        "message": f"runbook 已写入知识库 (待人工审核), id={rb_id}",
        "hint": "可在 Web 控制台 > 知识库管理 中审核此条目",
    }


@tool("restart_service")
def _restart_service(service="", reason="", node=""):
    """通过 CM API 启动停止的服务角色

    CM API commands/start 只启动 STOPPED 的角色, 不影响已运行的。
    """
    svc_info = SERVICE_MAP.get(service)
    if not svc_info:
        return {"error": f"未知服务: {service}",
                "available": list(SERVICE_MAP.keys())}

    cm_svc = svc_info["cm_service"]
    target_type = svc_info["cm_role_type"]
    target_nodes = _resolve_node(svc_info, node)
    target_hostnames = {_node_hostname(n) for n in target_nodes}

    # 先检查当前角色状态
    role_data = cm_get(f"/clusters/{CM_CLUSTER}/services/{cm_svc}/roles")
    stopped_roles = []
    running_roles = []
    for role in role_data.get("items", []):
        if role.get("type") != target_type:
            continue
        hostname = _resolve_hostname(role.get("hostRef", {}))
        if target_hostnames and hostname not in target_hostnames:
            continue
        state = role.get("roleState", "")
        if state in ("STOPPED", "DOWN", "UNKNOWN"):
            stopped_roles.append({"name": role.get("name", ""),
                                  "node": hostname, "state": state})
        else:
            running_roles.append({"name": role.get("name", ""),
                                  "node": hostname, "state": state})

    # 如果没有停止的角色, 直接返回
    if not stopped_roles:
        return {
            "service": service,
            "reason": reason,
            "risk_level": "high" if svc_info.get("core") else "medium",
            "result": "already_running",
            "message": f"所有 {service} 角色已在运行, 无需启动",
            "running": running_roles,
        }

    # 调用 CM API commands/start 启动停止的角色
    cmd_data = cm_post(
        f"/clusters/{CM_CLUSTER}/services/{cm_svc}/commands/start"
    )

    return {
        "service": service,
        "reason": reason,
        "risk_level": "high" if svc_info.get("core") else "medium",
        "command_id": cmd_data.get("id", ""),
        "command_name": cmd_data.get("name", ""),
        "stopped_before": stopped_roles,
        "already_running": running_roles,
        "result": "starting" if cmd_data.get("id") else "failed",
        "hint": "CM API 以服务为单位启动所有 stopped 角色 (不支持单节点精确重启). 请等待几秒后用 get_service_status 验证恢复",
    }


@tool("hdfs_admin")
def _hdfs_admin(action="", path="/"):
    """执行 HDFS 只读管理命令"""
    user = "hdfs"
    nn_node = SERVICE_MAP["NameNode"]["nodes"][0]
    ip = _node_ip(nn_node)

    # 安全: 校验 path 防止命令注入 (只允许合法 HDFS 路径)
    if not path or not path.startswith("/") or ".." in path:
        path = "/"
    safe_path = shlex.quote(path)
    cmds = {
        "report": f"sudo -u {user} hdfs dfsadmin -report 2>&1 | head -30",
        "fsck": f"sudo -u {user} hdfs fsck {safe_path} 2>&1 | tail -20",
        "ls": f"sudo -u {user} hdfs dfs -ls {safe_path} 2>&1",
        "du": f"sudo -u {user} hdfs dfs -du -h {safe_path} 2>&1",
    }
    cmd = cmds.get(action)
    if not cmd:
        return {"error": f"未知操作: {action}", "available": list(cmds.keys())}

    stdout, stderr, rc = ssh_exec(ip, cmd, timeout=30)
    return {
        "action": action,
        "path": path,
        "output": stdout if stdout else stderr,
        "returncode": rc,
    }


@tool("edit_remote_config")
def _edit_remote_config(service="", node="", file="", find="", replace="", reason=""):
    """修改远程配置文件 (§21.4 reversible: 先备份 .bak.<ts> 再 sed 替换 再 reload)

    Guardrail 确保走 reversible 档; 备份失败则中止, 替换失败则自动回滚。
    """
    if not all([service, node, file, find, replace]):
        return {"error": "参数缺失: 需 service/node/file/find/replace"}

    svc_info = SERVICE_MAP.get(service)
    if not svc_info:
        return {"error": f"未知服务: {service}", "available": list(SERVICE_MAP.keys())}

    ip = _node_ip(node)
    ts = int(time.time())
    safe_file = shlex.quote(file)

    # 1. 备份
    _, stderr, rc = ssh_exec(ip, f"cp {safe_file} {safe_file}.bak.{ts}", timeout=15)
    if rc != 0:
        return {"error": f"备份失败: {stderr}", "result": "failed"}

    # 2. 字面替换 (用 python3 避免 sed 正则注入, find/replace 经 shlex.quote 转义)
    py_script = (
        "import sys; f=sys.argv[1]; find=sys.argv[2]; repl=sys.argv[3];"
        "c=open(f).read(); n=c.count(find); "
        "open(f,'w').write(c.replace(find,repl)); print(n)"
    )
    replace_cmd = (
        f"python3 -c {shlex.quote(py_script)} {safe_file} "
        f"{shlex.quote(find)} {shlex.quote(replace)}"
    )
    stdout, stderr, rc = ssh_exec(ip, replace_cmd, timeout=15)
    if rc != 0:
        ssh_exec(ip, f"cp {safe_file}.bak.{ts} {safe_file}", timeout=15)
        return {"error": f"替换失败: {stderr}, 已回滚",
                "result": "failed", "backup": f"{file}.bak.{ts}"}

    # 3. reload 服务 (CM API commands/restart)
    cm_svc = svc_info["cm_service"]
    cmd_data = cm_post(f"/clusters/{CM_CLUSTER}/services/{cm_svc}/commands/restart")

    return {
        "service": service,
        "node": node,
        "file": file,
        "reason": reason,
        "backup": f"{file}.bak.{ts}",
        "replacements": stdout.strip() if stdout else "0",
        "command_id": cmd_data.get("id", ""),
        "result": "starting" if cmd_data.get("id") else "failed",
        "hint": "配置已修改并备份, CM 正在 reload 服务, 请等待后用 get_service_status 验证",
    }


# ============================================================
# 执行入口
# ============================================================

def execute_tool(name: str, arguments: dict) -> dict:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": f"未知工具: {name}"}
    risk = TOOL_RISK.get(name, RISK_LOW)
    # restart_service 按服务细分风险
    if name == "restart_service":
        svc = arguments.get("service", "")
        svc_info = SERVICE_MAP.get(svc, {})
        risk = RISK_HIGH if svc_info.get("core") else RISK_MEDIUM
    try:
        result = handler(**arguments)
        logger.info(f"TOOL {name} [risk={risk}] args={arguments} -> "
                    f"{json.dumps(result, ensure_ascii=False)[:300]}")
        return result
    except Exception as e:
        logger.error(f"TOOL {name} 执行异常: {e}")
        return {"error": str(e)}


# ---- 工具子集 (按 session 模式) ----
AUTO_TOOL_NAMES = [
    "get_service_status", "get_alerts", "get_metrics",
    "read_logs", "search_kb", "hdfs_admin",
]
# M5: fix 模式增加 write_runbook, 修复成功后可回写经验
FIX_TOOL_NAMES = AUTO_TOOL_NAMES + ["restart_service", "edit_remote_config", "write_runbook"]


def get_tool_definitions(names):
    return [d for d in TOOL_DEFINITIONS if d["function"]["name"] in names]


# ============================================================
# Demo 辅助: 故障注入 / 告警检测 / 集群快照
# (orchestrator.py 调用, 后续切 docker 环境可替换实现)
# ============================================================

def inject_fault(fault="datanode_oom"):
    """故障注入 — 当前由用户手动操作, 此函数为空操作占位

    后续可对接 MCP/skill 实现自动故障注入。
    """
    if fault == "none":
        logger.info("故障恢复: 确保所有服务运行 (空操作, 由用户手动管理)")
    else:
        logger.info(f"故障注入({fault}): 当前由用户手动操作, 请手动停止对应服务")


# 告警缓存: 避免 orchestrator 每 2s 轮询时对 CM 发起几十次请求
_alerts_cache = {"data": [], "ts": 0}
_ALERTS_CACHE_TTL = 10  # 秒


def get_pending_alerts():
    """获取当前待处理告警 (供 orchestrator 调度用), 带 10s 缓存避免 CM 轮询风暴"""
    now = time.time()
    if now - _alerts_cache["ts"] < _ALERTS_CACHE_TTL:
        return _alerts_cache["data"]
    result = _get_alerts()
    alerts = result.get("alerts", [])
    _alerts_cache["data"] = alerts
    _alerts_cache["ts"] = now
    return alerts


def get_cluster_snapshot():
    """获取集群服务快照 (供 orchestrator 状态卡用)"""
    services = {}
    for svc_name in INSPECT_SERVICES:
        svc_info = SERVICE_MAP.get(svc_name)
        if not svc_info:
            continue
        cm_svc = svc_info["cm_service"]
        role_data = cm_get(
            f"/clusters/{CM_CLUSTER}/services/{cm_svc}/roles"
        )
        roles = []
        for r in role_data.get("items", []):
            if r.get("type") == svc_info["cm_role_type"]:
                roles.append({
                    "node": _resolve_hostname(r.get("hostRef", {})),
                    "state": r.get("roleState", ""),
                    "health": r.get("healthSummary", ""),
                })
        # 汇总
        bad = [r for r in roles if r["health"] not in ("GOOD", "DISABLED")]
        services[svc_name] = {
            "health": "BAD" if bad else "GOOD",
            "role_count": len(roles),
        }
    # 全局健康状态
    bad_services = [k for k, v in services.items() if v["health"] != "GOOD"]
    overall_health = "BAD" if bad_services else "GOOD"
    alerts = get_pending_alerts()
    return {
        "overall_health": overall_health,
        "services": services,
        "alerts": len(alerts),
    }
