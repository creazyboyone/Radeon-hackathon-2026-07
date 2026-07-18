# AIOps Agent — 大数据集群自治运维

基于 LLM (Qwen 27B + ROCm) 的大数据平台自治运维 Agent，实现 24h 无人值守巡检 → 告警驱动诊断 → 安全护栏修复 → 验证恢复的全闭环。

## 架构

```
Orchestrator (master session, 常驻规则调度)
  ├── /auto 巡检 (ReAct agent, 15 轮)
  └── /fix 修复 (告警驱动抢占, ReAct agent, 15 轮)
         ├── 工具层: CM API + SSH (8 个工具)
         │   ├── 只读: get_service_status / get_alerts / get_metrics / read_logs / search_kb / hdfs_admin
         │   └── 写操作: restart_service (CM API) / edit_remote_config (备份→改→reload)
         ├── 安全护栏 (§21 双轴四档分级自治):
         │   ├── 轴1 AUTONOMY: supervised (人工审批) / autonomous (无人值守)
         │   ├── 轴2 tier: low / medium / recover / reversible / irreversible
         │   ├── 定级: risk_rules DB (页面可配) + classify() + fail-closed
         │   ├── recover: attempt 节流 (audit_log 派生计数) + 熔断 (类级跨会话累积)
         │   ├── reversible: 先备份 .bak.<ts> 再改再 reload
         │   └── irreversible: autonomous 模式立即拒绝 + 升级告警
         └── 事件总线 → WebSocket → Web 控制台 (实时 ReAct 循环)

LLM: llama.cpp server (Qwopus3.6-27B-v2-MTP Q4_K_M, ROCm gfx1100, 128k context)
     KV q8_0 + Flash Attention + MTP 投机解码 (n_max=1, 37.5 t/s, +30%)
DB: SQLite (sessions/events/cluster_state/audit_log/approvals/risk_rules)
Web: FastAPI + WebSocket (后端) / React + Vite + Ant Design (前端)
```

## 快速开始

### 1. 环境
```bash
# 后端依赖
pip install -r requirements.txt

# 前端依赖
cd web && npm install
```

### 2. 远程推理服务器 (AMD Radeon GPU)
```bash
# 上传并执行 bootstrap.sh (自动安装 SSH + modelscope 下载模型 + 启动 llama-server)
scp -P <PORT> scripts/bootstrap.sh root@<REMOTE_IP>:/workspace/
ssh -p <PORT> root@<REMOTE_IP> "sed -i 's/\r$//' /workspace/bootstrap.sh && bash /workspace/bootstrap.sh"

# SSH 隧道: 本地 18080 → 远程 llama-server 8080
ssh -o ServerAliveInterval=30 -L 18080:127.0.0.1:8080 -p <PORT> root@<REMOTE_IP> -N
```

### 3. 配置
```python
# src/secrets_local.py (不提交, 从 secrets_example.py 复制)
LLM_API_KEY = "<your_api_key>"
HADOOP_PASSWORD = "<your_password>"

# src/config.py (已默认配置, 按需修改)
LLM_BASE_URL = "http://127.0.0.1:18080/v1"
LLM_MODEL = "/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf"
CLUSTER_NODES = ["hadoop01", "hadoop02", "hadoop03"]
AUTONOMY = "supervised"  # 或 "autonomous" (无人值守)
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
| M1 推理基座 | ✅ | llama.cpp ROCm/HIPBLAS, Qwen27B Q4_K_M, MTP 投机解码 |
| M2 工具层 | ✅ | CM API + SSH, 8 个工具 (6 只读 + restart_service + edit_remote_config) |
| M3 编排层 | ✅ | Orchestrator 常驻 + /auto 巡检 + /fix 抢占 + SQLite 落库 |
| §21 安全护栏 | ✅ | 双轴四档 (AUTONOMY × tier) + risk_rules DB + classify + attempt 节流 |
| M5 KB 向量检索 | 待做 | sqlite-vec + bge-small |
| M6 Web 控制台 | ✅ | FastAPI+WebSocket 后端, React+Vite+AntDesign 前端 |
| M7 演示提交 | 待做 | 录屏 + 性能数据 |

## AMD Radeon GPU 推理优化

| 优化项 | 值 | 效果 |
|--------|-----|------|
| 模型 | Qwopus3.6-27B-v2-MTP Q4_K_M | 16GB, 27B 参数, 248K vocab |
| KV cache | q8_0 量化 | VRAM 节省 ~50% |
| Flash Attention | ON | 长序列加速 |
| MTP 投机解码 | `--spec-type draft-mtp --spec-draft-n-max 1` | 37.5 t/s (baseline 28.9, +30%) |
| Prompt cache | ON (8192 MiB) | 重复 prompt 加速 |
| Context | 131072 (128k) | 支持长上下文 |
| VRAM | 21.7G / 51.5G | 充裕 |

### MTP 基准测试

| 配置 | t/s (client) | 加速比 | 接受率 |
|------|-------------|--------|--------|
| baseline (无 MTP) | 28.9 | — | — |
| **n_max=1 (最优)** | **37.5** | **+30%** | 77.4% |
| n_max=2 | 34.2 | +18% | 61.0% |
| n_max=3 | 34.0 | +18% | 53.7% |
| n_max=5 | 31.7 | +10% | 44.6% |
| n_max=8 | 29.5 | +2% | 24.0% |

## Web 控制台

- **左侧菜单**: Agent 活动台 / 审批中心 / 风险规则 (可收缩)
- **Agent 活动台**: Session 树 (master→auto/fix) + Timeline 事件流 (Markdown 渲染 + 流式输出 + 折叠 JSON)
- **审批中心**: pending 列表 + 通过/拒绝 + 风险标签 + Badge 角标
- **风险规则**: risk_rules CRUD (irreversible 档禁勾 autonomous)
- **集群状态**: 选中 master session 显示服务健康状态卡 (overall_health + per-service)

## 安全护栏 (§21 双轴四档)

| 档位 | 典型操作 | autonomous 行为 | supervised 行为 |
|------|---------|---------------|----------------|
| low / medium | 只读 / 重启非核心 | 自动执行 | 自动执行 |
| recover | 重启已 DOWN 服务 | attempt 节流 → 自动执行 | 等人工审批 |
| reversible | 改配置 (先备份) | 自动执行 (强制备份) | 等人工审批 |
| irreversible | hdfs format / rm | **立即拒绝 + 升级告警** | 等人工审批 |

定级权归规则(DB), 不归模型。fail-closed: 未知工具一律 irreversible + 不可自动。

## 文件结构

```
src/
├── agent.py          # ReAct agent (巡检/修复, 流式输出)
├── orchestrator.py   # 常驻编排 (master session, /auto+/fix)
├── llm_client.py     # LLM 客户端 (chat + chat_stream SSE)
├── tools.py          # 8 个工具 (CM API + SSH)
├── guardrails.py     # 安全护栏 (§21 双轴四档 + classify + attempt 节流)
├── db.py             # SQLite Store (sessions/events/audit/approvals/risk_rules)
├── config.py         # 配置 (从 secrets_local.py 读敏感信息)
└── web/
    ├── app.py        # FastAPI (REST API + WebSocket + risk_rules CRUD)
    └── event_bus.py  # 事件总线 (queue.Queue 桥接同步/异步)
web/                  # React + Vite + Ant Design
├── src/
│   ├── App.tsx       # 布局 (Sider+Header+面包屑+Content)
│   ├── App.css      # 全局样式 + Markdown 渲染
│   └── components/
│       ├── AgentActivity.tsx  # Agent 活动台
│       ├── ApprovalCenter.tsx # 审批中心
│       └── RiskRules.tsx      # 风险规则管理
main.py               # 入口 (FastAPI 子线程 + orchestrator 主线程)
scripts/bootstrap.sh   # 远程推理服务器初始化
scripts/mtp_bench.py   # MTP 基准测试
docs/DESIGN.md         # 详细设计文档 (§21 安全护栏方案)
docs/TODO.md           # 项目进度总览
docs/README.md         # 项目说明 (本文件)
```

## 端到端验证

| 轮次 | 故障 | 诊断 | 修复 | 验证 |
|---|---|---|---|---|
| 第一轮 | DataNode 停 | ✅ 15 轮 ReAct | ❌ JAVA_HOME 缺失 | - |
| 第二轮 | NameNode 停 (SIGTERM) | ✅ 查日志→查KB→排除OOM→查jps | ✅ CM API commands/start | ✅ hdfs_admin report |