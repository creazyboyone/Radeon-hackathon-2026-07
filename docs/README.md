# AIOps Agent — 大数据集群自治运维

基于 LLM (Qwen 27B + ROCm) 的大数据平台自治运维 Agent，实现 24h 无人值守巡检 → 告警驱动诊断 → 安全护栏修复 → 验证恢复的全闭环。

## 架构

```
Orchestrator (master session, 常驻规则调度)
  ├── /auto 巡检 (ReAct agent, 15 轮)
  └── /fix 修复 (告警驱动抢占, ReAct agent, 15 轮)
         ├── 工具层: CM API + SSH (get_service_status/get_alerts/get_metrics/read_logs/search_kb/restart_service/hdfs_admin)
         ├── 安全护栏: 风险分级 + dry-run + 审批门 + 审计日志 + 熔断
         └── 事件总线 → WebSocket → Web 控制台 (实时 ReAct 循环)

LLM: llama.cpp server (Qwopus3.6-27B Q4_K_M, ROCm gfx1100, 128k context, KV q8 + FA)
DB: SQLite (sessions/events/cluster_state/audit_log/approvals)
Web: FastAPI + WebSocket (后端) / React + Vite + Ant Design (前端)
```

## 快速开始

### 1. 环境
```bash
pip install -r requirements.txt
cd web && npm install
```

### 2. 远程推理服务器 (AMD Radeon GPU)
```bash
# 启动推理服务器 (详见 scripts/bootstrap.sh)
# SSH 隧道: 本地 18080 → 远程 llama-server 8080
ssh -o ServerAliveInterval=30 -L 18080:127.0.0.1:8080 -p <PORT> root@<REMOTE_IP> -N
```

### 3. 配置
```python
# src/secrets_local.py (不提交)
LLM_API_KEY = "<your_api_key>"
HADOOP_PASSWORD = "<your_password>"
# src/config.py
LLM_BASE_URL = "http://127.0.0.1:18080/v1"
CLUSTER_NODES = ["hadoop01", "hadoop02", "hadoop03"]
```

### 4. 启动
```bash
# 后端 (API + WebSocket + orchestrator 巡检)
python main.py

# 前端 (另一个终端)
cd web && npm run dev
# 打开 http://localhost:3000
```

## 功能模块

| 模块 | 状态 | 说明 |
|---|---|---|
| M2 工具层 | ✅ | CM API + SSH 真实实现, 7 个工具 |
| M3 编排层 | ✅ | Orchestrator 常驻 + /auto 巡检 + /fix 抢占 + SQLite 落库 |
| M4 安全护栏 | ✅ | 风险分级/dry-run/审批门/审计日志/熔断 |
| M5 KB 向量检索 | 待做 | sqlite-vec + bge-small |
| M6 Web 控制台 | ✅ | FastAPI+WebSocket 后端, React+Vite+AntDesign 前端 |
| M7 演示提交 | 待做 | 录屏 + README + 性能数据 |

## Web 控制台

- **登录页**: 用户名密码 (localStorage)
- **左侧菜单**: Agent 活动台 / 审批中心 (可收缩)
- **Agent 活动台**: Session 树 (master→auto/fix) + Timeline 事件流 (Markdown 渲染 + 流式输出 + 折叠 JSON)
- **审批中心**: pending 列表 + 通过/拒绝 + 风险标签
- **集群状态**: 选中 master session 显示服务健康状态卡

## 文件结构

```
src/
├── agent.py          # ReAct agent (巡检/修复, 流式输出)
├── orchestrator.py   # 常驻编排 (master session, /auto+/fix)
├── llm_client.py     # LLM 客户端 (chat + chat_stream SSE)
├── tools.py          # 7 个工具 (CM API + SSH)
├── guardrails.py     # 安全护栏 (风险分级/审批/审计/熔断)
├── db.py             # SQLite Store (sessions/events/audit/approvals)
├── config.py         # 配置 (从 secrets_local.py 读敏感信息)
└── web/
    ├── app.py        # FastAPI (REST API + WebSocket)
    └── event_bus.py  # 事件总线 (queue.Queue 桥接同步/异步)
web/                  # React + Vite + Ant Design
├── src/
│   ├── App.tsx       # 布局 (Sider+Header+面包屑+Content)
│   ├── App.css      # 全局样式 + Markdown 渲染
│   └── components/
│       ├── AgentActivity.tsx  # Agent 活动台
│       └── ApprovalCenter.tsx # 审批中心
main.py               # 入口 (FastAPI 子线程 + orchestrator 主线程)
scripts/bootstrap.sh   # 远程推理服务器初始化 (sshd + modelscope + llama-server)
docs/DESIGN.md         # 详细设计文档
docs/README.md         # 项目说明 (本文件)
```

## 端到端验证

| 轮次 | 故障 | 诊断 | 修复 | 验证 |
|---|---|---|---|---|
| 第一轮 | DataNode 停 | ✅ 15 轮 ReAct | ❌ JAVA_HOME 缺失 | - |
| 第二轮 | NameNode 停 (SIGTERM) | ✅ 查日志→查KB→排除OOM→查jps | ✅ CM API commands/start | ✅ hdfs_admin report |
