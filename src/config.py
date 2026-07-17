import os

# ============================================================
# 加载本地秘密配置 (不提交到 git, 见 secrets.example.py)
# ============================================================
try:
    from . import secrets_local  # noqa: F401 — 设置环境变量
except ImportError:
    pass

# ============================================================
# LLM 推理配置
# ============================================================
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "CHANGE_ME")
LLM_MODEL = os.getenv("LLM_MODEL", "/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf")

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "aiops.db"))

# ---- Web 控制台认证 ----
# 设置 CONSOLE_TOKEN 后, 所有 API 请求需携带 Authorization: Bearer <token>
# 留空则不启用认证 (仅限开发环境)
CONSOLE_TOKEN = os.getenv("CONSOLE_TOKEN", "")

MAX_REACT_ITERATIONS = 15
MAX_TOKENS = 2048
TEMPERATURE = 0.7

# ============================================================
# 集群配置 (配置驱动, 切换环境只改这里)
#
# 当前: 临时使用现有 CDH 6.3.2 三节点集群
# 后续: 切换为 docker-compose 搭建的 Apache Hadoop + Prometheus + Grafana
# 切换时: 1) 改 CLUSTER_BACKEND 2) 改 CLUSTER_NODES 3) 改 SERVICE_MAP 4) 改 MONITOR 配置
# ============================================================

# 后端类型: "cdh" (Cloudera Manager API) 或 "apache" (docker-compose, 待搭建)
CLUSTER_BACKEND = os.getenv("CLUSTER_BACKEND", "cdh")

# SSH 配置
SSH_USER = os.getenv("SSH_USER", "root")
SSH_PORT = int(os.getenv("SSH_PORT", "22"))
SSH_OPTS = "-o BatchMode=yes -o ConnectTimeout=10"

# 集群节点 (node_key -> IP + hostname, IP 从 secrets.local.py 读取)
CLUSTER_NODES = {
    "hadoop01": {"host": os.getenv("NODE01_HOST", "10.0.0.1"),
                 "hostname": os.getenv("NODE01_NAME", "hadoop01")},
    "hadoop02": {"host": os.getenv("NODE02_HOST", "10.0.0.2"),
                 "hostname": os.getenv("NODE02_NAME", "hadoop02")},
    "hadoop03": {"host": os.getenv("NODE03_HOST", "10.0.0.3"),
                 "hostname": os.getenv("NODE03_NAME", "hadoop03")},
}

# ---- Cloudera Manager API (CDH 后端用, apache 后端可忽略) ----
CM_HOST = os.getenv("CM_HOST", "10.0.0.3")
CM_PORT = int(os.getenv("CM_PORT", "7180"))
CM_USER = os.getenv("CM_USER", "admin")
CM_PASS = os.getenv("CM_PASS", "CHANGE_ME")
CM_CLUSTER = os.getenv("CM_CLUSTER", "test")
CM_API_VERSION = os.getenv("CM_API_VERSION", "v30")

# ---- 监控配置 (后续 docker 搭建 Prometheus + Grafana) ----
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://10.0.0.3:9090")
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://10.0.0.3:9093")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://10.0.0.3:3000")

# ============================================================
# 服务映射表 (核心: 服务名 -> 节点/角色/日志路径/运行用户)
#
# 这个表驱动所有工具的 SSH 命令构造和日志查找。
# 切换 docker 环境时只需改这个表。
# ============================================================
SERVICE_MAP = {
    # ---- HDFS ----
    "NameNode": {
        "cm_service": "hdfs",
        "cm_role_type": "NAMENODE",
        "log_dir": "/var/log/hadoop-hdfs",
        "log_prefix": "hadoop-cmf-hdfs-NAMENODE",
        "nodes": ["hadoop02"],
        "run_user": "hdfs",
        "core": True,          # 核心服务, 重启判高危
    },
    "SecondaryNameNode": {
        "cm_service": "hdfs",
        "cm_role_type": "SECONDARYNAMENODE",
        "log_dir": "/var/log/hadoop-hdfs",
        "log_prefix": "hadoop-cmf-hdfs-SECONDARYNAMENODE",
        "nodes": ["hadoop01"],
        "run_user": "hdfs",
        "core": False,
    },
    "DataNode": {
        "cm_service": "hdfs",
        "cm_role_type": "DATANODE",
        "log_dir": "/var/log/hadoop-hdfs",
        "log_prefix": "hadoop-cmf-hdfs-DATANODE",
        "nodes": ["hadoop01", "hadoop02", "hadoop03"],
        "run_user": "hdfs",
        "core": False,         # 非核心, 可中危重启
    },
    # ---- YARN ----
    "ResourceManager": {
        "cm_service": "yarn",
        "cm_role_type": "RESOURCEMANAGER",
        "log_dir": "/var/log/hadoop-yarn",
        "log_prefix": "hadoop-cmf-yarn-RESOURCEMANAGER",
        "nodes": ["hadoop02"],
        "run_user": "yarn",
        "core": True,
    },
    "NodeManager": {
        "cm_service": "yarn",
        "cm_role_type": "NODEMANAGER",
        "log_dir": "/var/log/hadoop-yarn",
        "log_prefix": "hadoop-cmf-yarn-NODEMANAGER",
        "nodes": ["hadoop01", "hadoop02", "hadoop03"],
        "run_user": "yarn",
        "core": False,
    },
    "JobHistoryServer": {
        "cm_service": "yarn",
        "cm_role_type": "JOBHISTORY",
        "log_dir": "/var/log/hadoop-yarn",
        "log_prefix": "hadoop-cmf-yarn-JOBHISTORY",
        "nodes": ["hadoop01"],
        "run_user": "yarn",
        "core": False,
    },
    # ---- Hive ----
    "HiveMetaStore": {
        "cm_service": "hive",
        "cm_role_type": "HIVEMETASTORE",
        "log_dir": "/var/log/hive",
        "log_prefix": "hadoop-cmf-hive-HIVEMETASTORE",
        "nodes": ["hadoop01"],
        "run_user": "hive",
        "core": False,
    },
    "HiveServer2": {
        "cm_service": "hive",
        "cm_role_type": "HIVESERVER2",
        "log_dir": "/var/log/hive",
        "log_prefix": "hadoop-cmf-hive-HIVESERVER2",
        "nodes": ["hadoop01"],
        "run_user": "hive",
        "core": False,
    },
    # ---- ZooKeeper ----
    "ZooKeeper": {
        "cm_service": "zookeeper",
        "cm_role_type": "SERVER",
        "log_dir": "/var/log/zookeeper",
        "log_prefix": "hadoop-cmf-zookeeper-SERVER",
        "nodes": ["hadoop01", "hadoop02", "hadoop03"],
        "run_user": "zookeeper",
        "core": False,
    },
    # ---- Oozie ----
    "Oozie": {
        "cm_service": "oozie",
        "cm_role_type": "OOZIE_SERVER",
        "log_dir": "/var/log/oozie",
        "log_prefix": "hadoop-cmf-oozie-OOZIE_SERVER",
        "nodes": ["hadoop01"],
        "run_user": "oozie",
        "core": False,
    },
}

# 默认巡检服务列表 (agent /auto 模式检查这些)
INSPECT_SERVICES = [
    "NameNode", "DataNode", "ResourceManager", "NodeManager",
    "HiveMetaStore", "ZooKeeper",
]

# Java 路径 (CDH 环境的 jdk)
JAVA_BIN = "/usr/java/jdk1.8u372-b07-cloudera/bin/java"
JPS_BIN = "/usr/java/jdk1.8u372-b07-cloudera/bin/jps"

# CDH parcel 路径 (用于 SSH 执行 daemon 脚本启停服务)
CDH_PARCEL = "/opt/cloudera/parcels/CDH-6.3.2-1.cdh6.3.2.p0.1605554"
HADOOP_SBIN = f"{CDH_PARCEL}/lib/hadoop/sbin"
YARN_SBIN = f"{CDH_PARCEL}/lib/hadoop-yarn/sbin"
