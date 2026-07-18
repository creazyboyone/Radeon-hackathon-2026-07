# M7 冲刺路线图：部署形态 · 平台迁移 · 测试策略

> 生成时间：2026-07-18  
> 适用阶段：项目核心功能接近完善后的收尾冲刺（提交截止 2026-08-06，约 19 天）  
> 关联文档：[CRITIQUE.md](./CRITIQUE.md)（问题审查）· [DESIGN.md](./DESIGN.md)（设计）· [TODO.md](./TODO.md)（进度）

本文档记录三项决策的最终结论与执行计划：**部署形态** · **Apache Hadoop 平台迁移** · **系统性测试策略**。

---

## 目录

1. [硬件资源与约束](#1-硬件资源与约束)
2. [部署形态决策](#2-部署形态决策)
3. [监控栈决策（Prometheus + Alertmanager + Grafana）](#3-监控栈决策prometheus--alertmanager--grafana)
4. [Apache Hadoop 平台迁移](#4-apache-hadoop-平台迁移)
5. [系统性测试策略](#5-系统性测试策略)
6. [执行优先级与里程碑](#6-执行优先级与里程碑)

---

## 1. 硬件资源与约束

| 资源 | 规格 | 用途 | 关键约束 |
|------|------|------|---------|
| **本地 PC** | RX 7900 XTX（24GB VRAM）+ 32GB RAM，Windows | 跑 Hadoop Docker + Agent | ROCm on Windows 支持不完整（需 WSL2） |
| **AMD 云服务器** | W7900D（48GB VRAM）+ 503GB RAM，Ubuntu | 已跑 llama-server，37.5 t/s | **k8s Pod 绑 GPU + PVC，无法跑 Docker-in-Docker** |
| **CDH 测试集群** | 3 节点 ×（15GB RAM + 300GB），192.168.6.176-178 | 当前临时集群 | 内网、评委无法访问、不可复现 |

> 注：这里的"本地"均指 RX 7900 XTX 那台 PC，非开发用的这台笔记本。

### 核心约束推导

- **云端不能跑多节点 Hadoop**：Pod 无法嵌套 Docker，云端最多只能 `apt-get` 装单节点 Apache Hadoop，但 Pod 重启数据丢失、评委无法登录、Pod 回收后环境消失 → **只适合录视频时临时用，不能作为提交方案**。
- **评委复现 ≠ 登录你的机器**：评委需要能在**他们自己的机器**上 `git clone` + `docker-compose up` 跑起来。docker-compose 本身就是跨机器复现方案。
- **LLM endpoint 是唯一外部依赖**：把它做成环境变量，就与部署解耦。

---

## 2. 部署形态决策

### 2.1 三方案对比

| 方案 | Hadoop | Agent+Web+DB | 推理 | 评委可复现 | 障碍 |
|------|--------|--------------|------|-----------|------|
| A: 全云 | 云 Pod 单节点 | 云 | 云 W7900D | ❌ Pod 无法交付 | 违反"本地化"、无法复现 |
| **B: 云推理 + 本地全栈**（推荐） | 本地 Docker×3 | 本地 PC | 云 W7900D | ✅ | 依赖云服务器在线 |
| C: 全本地 | 本地 Docker×3 | 本地 PC | 本地 7900XTX | ✅ 最自洽 | ROCm on Windows 折腾成本高 |

### 2.2 结论：采用方案 B（云推理 + 本地全栈）

**理由：**
1. Q4_K_M 的 27B 模型约 16GB，7900XTX 24GB 装得下，但 ROCm on Windows 目前需要 WSL2，支持不完整，折腾风险大。
2. 云 W7900D 已跑通，37.5 t/s 有实测数据——**这才是比赛的 AMD 硬件亮点**，不应放弃。
3. 评委只需 `git clone` + `docker-compose up` + 配一个 `.env` 指向推理地址即可完整复现。
4. 推理地址做成 `LLM_BASE_URL` 环境变量，录视频时指向云 W7900D，评委复现时可指向他们自己的 GPU 端点或你提供的公开地址。

> 方案 C 作为备选保留：若临近提交时云服务器不稳定，可切到本地 7900XTX（需提前验证 WSL2 + ROCm 通路）。

### 2.3 最终架构图

```
┌────────────────────────────────────────────────────────────────┐
│  任意一台有 Docker 的机器                                          │
│  （录视频时 = 本地 7900XTX PC；评委复现 = 评委自己的机器）           │
│                                                                  │
│  $ git clone ... && cd ... && cp .env.example .env               │
│  $ docker-compose up                                             │
│                                                                  │
│  ┌──────────────── docker-compose 编排 ────────────────┐         │
│  │  namenode          Apache Hadoop 3.x                │         │
│  │  datanode1                                          │         │
│  │  datanode2                                          │         │
│  │  prometheus        + hadoop jmx_exporter            │         │
│  │  alertmanager      告警源（get_alerts 从这里拿）      │         │
│  │  grafana           可选，纯展示                       │         │
│  │  aiops-agent       Python + FastAPI + SQLite        │         │
│  │  aiops-web         React (nginx)                    │         │
│  └─────────────────────────────────────────────────────┘        │
│                                                                  │
│  .env:  LLM_BASE_URL=http://<推理地址>:8080/v1                    │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTP（可配置，唯一外部依赖）
                               ▼
                    AMD 云 W7900D（或评委自己的 GPU 端点）
                    llama-server :8080   —— 37.5 t/s, MTP 投机解码
```

### 2.4 各场景映射

| 场景 | LLM 指向 | Hadoop+Agent+Web | "本地化"要求 |
|------|---------|------------------|-------------|
| 演示视频录制 | 云 W7900D（展示 rocm-smi + 37.5 t/s） | 本地 PC Docker | ✅ 均本地容器运行 |
| 评委复现 | 评委自己的端点 / 公开地址 | 评委机器 Docker | ✅ |
| AMD 硬件展示 | 视频中展示 W7900D 参数 + 推理性能 | — | ✅ |

---

## 3. 监控栈决策（Prometheus + Alertmanager + Grafana）

### 3.1 结论：采用 Prometheus + Alertmanager，`get_alerts` 从 Alertmanager API 拿；Grafana 可选

### 3.2 理由

| 维度 | 说明 |
|------|------|
| 已在规划 | `config.py` 已预留 `PROMETHEUS_URL` / `ALERTMANAGER_URL` / `GRAFANA_URL` |
| 标准 API | Alertmanager 有 `GET /api/v2/alerts` 返回结构化告警列表，比 SSH 轮询干净可靠 |
| 评分价值 | Prometheus 是业界标准，直观展示"生产级 AIOps"架构完整性 |
| 实现更简单 | `get_alerts()` 改成一个 HTTP 调用，比现在遍历 CM API 还简单 |
| 不重复造轮子 | 自己 SSH 轮询进程 + 解析日志判断告警 = 重造 Alertmanager，且不可靠 |

### 3.3 组件职责

```
Hadoop JVM ──(jmx_exporter java agent)──► Prometheus ──(告警规则)──► Alertmanager
                                              │                          │
                                          Grafana(可选,展示)      get_alerts() HTTP 拉取
```

- **jmx_exporter**：以 java agent 形式挂到 NameNode/DataNode/RM/NM 的 JVM 上，暴露 JMX 指标为 Prometheus 格式。
- **Prometheus**：抓取指标 + 评估告警规则（DataNode 掉线、HDFS 容量、JVM 堆 OOM、NodeManager 失联等）。
- **Alertmanager**：告警聚合、去重、静默，暴露 `/api/v2/alerts`。
- **Grafana**：可选，纯可视化，agent 逻辑不依赖。有时间再加，录视频时是加分项。

### 3.4 需要编写的告警规则（示例）

| 规则名 | 表达式（示意） | 严重性 |
|--------|--------------|--------|
| DataNodeDown | `up{job="hdfs-datanode"} == 0` | critical |
| HDFSCapacityHigh | `hdfs_capacity_used_percent > 85` | warning |
| JVMHeapHigh | `jvm_memory_used / jvm_memory_max > 0.9` | warning |
| NodeManagerUnhealthy | `yarn_nodemanager_healthy == 0` | critical |
| NameNodeGCPause | `jvm_gc_pause_seconds > 5` | warning |

---

## 4. Apache Hadoop 平台迁移

### 4.1 现状

`config.py` 已预留 `CLUSTER_BACKEND` 开关（`"cdh"` / `"apache"`），迁移的核心工作在 `tools.py` 工具层的分支实现。

### 4.2 工具迁移工作量

| 工具 | CDH 现做法 | Apache Hadoop 替换方案 | 工作量 |
|------|-----------|----------------------|--------|
| `get_service_status` | CM API `/roles` | YARN RM REST API (`/ws/v1/cluster/nodes`) + HDFS JMX (`/jmx`) | 中 |
| `get_alerts` | CM healthChecks | **Alertmanager API `/api/v2/alerts`** | 中（见 §3） |
| `restart_service` | CM API `commands/start` | SSH + `$HADOOP_HOME/sbin/hadoop-daemon.sh start/stop` 或 `docker restart <container>` | 中 |
| `get_metrics` | SSH `free/df/top` | 不变，可加 YARN RM REST API 队列指标 | 小 |
| `read_logs` | SSH tail | 不变（仅日志路径改为 Apache 布局） | 小 |
| `hdfs_admin` | SSH hdfs 命令 | 不变 | 无 |
| `edit_remote_config` | SSH + 备份 + 替换 | 不变（配置文件路径改为 Apache 布局） | 小 |

**关键差异**：`get_alerts` 改动最大——CDH 的健康检查是 CM 内置的，Apache 需要靠 Prometheus 告警规则来定义。这正好用 §3 的监控栈解决。

### 4.3 Docker 镜像选型

- Hadoop：`apache/hadoop:3.x` 官方镜像，或 `bde2020/hadoop-*` 系列（社区成熟，docker-compose 示例多）。
- 3 节点最小配置：1 NameNode（含 RM）+ 2 DataNode（含 NodeManager）。
- 数据持久化：docker volume 挂载 `/hadoop/dfs`。

### 4.4 SERVICE_MAP 适配

Apache 布局与 CDH 差异：
- 日志目录：`$HADOOP_HOME/logs/` 而非 `/var/log/hadoop-hdfs`
- 日志文件名：`hadoop-<user>-<role>-<host>.log` 而非 `hadoop-cmf-hdfs-NAMENODE`
- 启停脚本：`sbin/hadoop-daemon.sh` / `sbin/yarn-daemon.sh`
- 无 parcel 路径，`HADOOP_HOME` 为容器内标准路径

建议：新增 `SERVICE_MAP_APACHE` dict，`config.py` 根据 `CLUSTER_BACKEND` 选择加载，避免污染现有 CDH 配置。

### 4.5 演示故障点设计（Demo 用）

为完美展现 agent 能力，docker-compose 环境需要能**一键注入故障**。可复现的故障场景：

| 故障 | 注入方式 | Agent 预期行为 |
|------|---------|---------------|
| DataNode 宕机 | `docker stop datanode2` | 检测告警 → 诊断 → 重启容器 → 验证 → 写 runbook |
| 磁盘写满 | 容器内 `fallocate -l 大文件` | 检测 HDFS 容量告警 → 定位 → 清理/扩容建议 |
| JVM OOM | 调低堆参数 + 压测 | 检测 OOM → 定位 → 调整堆 → 重启 |
| 配置错误 | `edit_remote_config` 反向演示 | 检测 → 回滚（配合 CRITIQUE F3） |

> 对应 [CRITIQUE.md](./CRITIQUE.md) §3.5：`inject_fault` 当前是空函数，应实现为 `docker stop/start` 或容器内命令注入。

---

## 5. 系统性测试策略

> 背景：目前只为 MVP 跑了两次，远不足以暴露问题。所有逻辑需符合设计预想，需实测验证。

### 5.1 三层测试体系

```
层3  端到端集成测试   ← 接 Docker Hadoop，真实跑通 auto/fix/学习循环
      ▲
层2  Guardrail 逻辑测试 ← 四档决策路径、熔断器、autonomy 切换
      ▲
层1  单工具测试        ← mock 响应，验证参数解析/命令拼接/异常处理
```

### 5.2 层1：单工具测试（最快，优先做）

对每个工具 mock 响应，验证参数解析、SSH 命令拼接、异常处理。

| 工具 | 测试重点 |
|------|---------|
| `edit_remote_config` | 注入风险（find/replace 含特殊字符）、备份是否生成、配置语法校验 |
| `restart_service` | 对 stopped / running / unhealthy 三种状态的判断分支 |
| `read_logs` | tail_n 边界、filter 转义、错误/警告计数正确性 |
| `hdfs_admin` | path 校验（`..`、非 `/` 开头、超长）拦截 |
| `get_metrics` | 各 metric 类型（free/df/top/jps）解析 |
| `search_kb` | 三种模式（hybrid/bm25/static_fallback）降级路径 |
| `write_runbook` | confidence 门控（≥0.7 通过，<0.7 拒绝） |

### 5.3 层2：Guardrail 逻辑测试

验证 §21 双轴四档的每条决策路径。

| 测试项 | 验证内容 |
|--------|---------|
| 四档分类 | low/medium/recover/reversible/irreversible 各自的 execute 路径 |
| fail-closed | 未知工具 → irreversible + block |
| 熔断器 | 连续 3 次失败后第 4 次被拦截；300s 冷却后恢复 |
| 尝试节流 | 600s 窗口内同一 (tool, service) 最多 2 次、间隔 60s |
| autonomy 切换 | supervised 下等审批（超时行为）；autonomous 下按档位直接执行/拒绝 |
| restart 精化 | `_refine_restart` 对 STOPPED→recover、RUNNING-unhealthy→irreversible 的降级 |
| 审计完整性 | classified/executed/rejected 三种状态都写入 audit_log |

> 关联 [CRITIQUE.md](./CRITIQUE.md) §1.3：熔断器 `_failure_counts` 非线程安全，测试并发场景时需注意竞态。

### 5.4 层3：端到端集成测试（接 Docker Hadoop）

| 场景 | 步骤 | 通过标准 |
|------|------|---------|
| auto 巡检（健康） | 正常集群 → 触发 `/auto` | 输出结构化健康报告，无误报 |
| fix 修复（DataNode） | `docker stop datanode2` → 等待告警 | 检测 → 诊断 → 重启 → 验证恢复 → 写 runbook |
| M5 学习循环 | fix 成功后 | runbook 出现在知识库，`search_kb` 可命中 |
| 审批流（supervised） | 触发高危操作 | Web 审批中心出现请求，批准后继续执行 |
| 熔断保护 | 连续制造修复失败 | 第 4 次被熔断器拦截，不再重试 |
| autonomous 模式 | 切 `AUTONOMY=autonomous` | 按四档策略自动执行，无需人工 |
| 告警聚合 | 同服务多告警 | 合并为一个 fix 任务（配合 CRITIQUE F2） |

### 5.5 测试产物

- `tests/test_tools.py` — 层1 单工具测试
- `tests/test_guardrails.py` — 层2 护栏逻辑测试
- `tests/test_e2e.py` 或 `scripts/e2e_demo.sh` — 层3 端到端（需 Docker 环境）
- 现有 `tests/test_m5_kb.py` 已覆盖知识库，保留

---

## 6. 执行优先级与里程碑

### 6.1 执行顺序

```
第一步：修复 P0/P1 Bug（保证系统能跑起来）
        ├── app.py ImportError（1 行）           [CRITIQUE 1.1]
        ├── Runbook 向量清空 bug（1 行）          [CRITIQUE 1.2]
        └── bootstrap.sh 密钥清理 + rotate        [CRITIQUE 4.1]

第二步：搭 docker-compose 环境（一切测试的基础，M7 blocking 项）
        ├── Apache Hadoop 3 节点（namenode + 2 datanode）
        ├── Prometheus + jmx_exporter + Alertmanager
        ├── aiops-agent + aiops-web 容器化
        └── .env.example + 一键启动验证

第三步：改 tools.py 支持 CLUSTER_BACKEND=apache
        ├── get_service_status → YARN RM REST + HDFS JMX
        ├── get_alerts → Alertmanager API          [核心改动]
        ├── restart_service → docker restart / daemon 脚本
        └── SERVICE_MAP_APACHE 适配

第四步：实现故障注入 + 三层系统性测试
        ├── inject_fault → docker stop/start        [CRITIQUE 3.5]
        ├── 层1 单工具测试
        ├── 层2 Guardrail 逻辑测试
        └── 层3 端到端集成测试

第五步：Demo 打磨 + 录制
        ├── 一键 demo 脚本
        ├── GPU 监控展示（amdsmi，AMD 主题）        [CRITIQUE F8]
        ├── 健康总览 Dashboard                      [CRITIQUE 6.1]
        └── 视频录制 + README 完善
```

### 6.2 里程碑对齐（TODO.md）

| 里程碑 | 本路线图对应 | 状态 |
|--------|------------|------|
| docker-compose 3 节点 Hadoop（替换 CDH） | 第二 + 三步 | ⏳ 待办（最高优先级） |
| M7 Demo + 提交（录制、性能数据、docker 环境） | 第五步 | ⏳ 待办 |

### 6.3 关键决策备忘

1. **推理不下本地**：保留云 W7900D，突出 AMD 硬件性能（37.5 t/s / MTP），方案 C 仅作备选。
2. **监控用 Prometheus 栈**：`get_alerts` 从 Alertmanager API 拿，不自己造轮子。
3. **复现靠 docker-compose**：LLM endpoint 做成环境变量，是唯一外部依赖。
4. **云端 Pod 不跑 Hadoop**：Pod 无法嵌套 Docker，只跑 llama-server。
5. **测试要实测三层**：MVP 两次远不够，单工具 → 护栏 → 端到端逐层验证。

---

*本路线图为 2026-07-18 讨论结论的落地记录，随执行进展更新。*
