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
import requests

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
            "description": "重启大数据服务(高危操作). 需提供理由. 核心服务(NameNode/ResourceManager)判高危, 非核心服务(DataNode/NodeManager)可自动执行",
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
]

# ---- 风险映射 ----
TOOL_RISK = {
    "get_service_status": RISK_LOW,
    "get_alerts": RISK_LOW,
    "get_metrics": RISK_LOW,
    "read_logs": RISK_LOW,
    "search_kb": RISK_LOW,
    "restart_service": RISK_HIGH,      # 默认高危, 实际按 SERVICE_MAP[svc].core 细分
    "hdfs_admin": RISK_LOW,
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


def cm_get(path):
    """CM API GET 请求"""
    url = f"http://{CM_HOST}:{CM_PORT}/api/{CM_API_VERSION}{path}"
    try:
        resp = requests.get(url, auth=(CM_USER, CM_PASS), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"CM API GET {path} failed: {e}")
        return {}


def cm_post(path):
    """CM API POST 请求 (重启/停止等)"""
    url = f"http://{CM_HOST}:{CM_PORT}/api/{CM_API_VERSION}{path}"
    try:
        resp = requests.post(url, auth=(CM_USER, CM_PASS), timeout=30)
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
    """检索运维知识库 — 当前简单关键词匹配, 后续接入 sqlite-vec 向量检索"""
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
        {"title": "YARN NodeManager 掉线排查",
         "content": "NodeManager掉线原因: 1.进程崩溃(OOM) 2.网络不通 3.磁盘满. "
                    "排查: yarn node -list确认状态, 查NodeManager日志. "
                    "修复: 重启NodeManager, 检查nodemanager.local-dirs磁盘空间"},
        {"title": "HDFS 磁盘满处理",
         "content": "磁盘满处理: 1.df -h确认 2.清理临时文件/日志 "
                    "3.必要时扩容. 注意: 不要直接删HDFS数据块, 用hdfs balancer重平衡"},
        {"title": "ZooKeeper 连接超时排查",
         "content": "ZK连接超时: 1.检查ZK服务状态 2.检查网络 3.检查sessionTimeout配置 "
                    "4.检查客户端连接数. 修复: 重启异常ZK节点, 调大tickTime/sessionTimeout"},
    ]
    results = [kb for kb in _kb
               if any(w in kb["title"] or w in kb["content"]
                      for w in query.split())]
    return {"query": query, "matches": len(results), "results": results}


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
        "hint": "CM 正在启动停止的角色, 请等待几秒后用 get_service_status 验证恢复",
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
FIX_TOOL_NAMES = AUTO_TOOL_NAMES + ["restart_service"]


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


def get_pending_alerts():
    """获取当前待处理告警 (供 orchestrator 调度用)"""
    result = _get_alerts()
    return result.get("alerts", [])


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
            "roles": roles,
        }
    alerts = get_pending_alerts()
    return {
        "services": {k: v["health"] for k, v in services.items()},
        "alerts": len(alerts),
    }
