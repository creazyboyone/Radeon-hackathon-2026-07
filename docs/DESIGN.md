# AIOps-Agent 设计文档

> 本文档为开发权威参考，后续开发以本文为准。
> 赛事：AMD AI DevMaster Hackathon - 赛道2 Agentic AI
> 最后更新：2026-07-14

---

## 0. 目录

1. [项目定位与赛题契合](#1-项目定位与赛题契合)
2. [场景定义](#2-场景定义)
3. [评分拆解与策略](#3-评分拆解与策略)
4. [运行环境与推理优化](#4-运行环境与推理优化)
5. [核心设计：24h 无人值守 + 分级审批](#5-核心设计24h-无人值守--分级审批)
6. [系统架构总览](#6-系统架构总览)
7. [编排层：Orchestrator + master + 子session](#7-编排层orchestrator--master--子session)
8. [上下文策略：一次性 context + DB 状态传递](#8-上下文策略一次性-context--db-状态传递)
9. [安全护栏](#9-安全护栏)
10. [工具层：MCP server](#10-工具层mcp-server)
11. [监控平台对接](#11-监控平台对接)
12. [Web 控制台](#12-web-控制台)
13. [Session 记录与回看](#13-session-记录与回看)
14. [知识库 RAG](#14-知识库-rag)
15. [技术选型](#15-技术选型)
16. [核心数据流](#16-核心数据流)
17. [数据模型（SQLite 表结构）](#17-数据模型sqlite-表结构)
18. [故障剧本（演示用）](#18-故障剧本演示用)
19. [开发顺序与里程碑](#19-开发顺序与里程碑)
20. [待办与开放问题](#20-待办与开放问题)

---

## 1. 项目定位与赛题契合

### 1.1 赛道要求

赛道2 Agentic AI 要求构建具备 reasoning / planning / **tool use** / memory / task execution 的智能体，示例涵盖 enterprise copilot、workflow automation、local RAG assistant、multi-agent system。评分：

| 维度 | 分值 | 说明 |
|---|---|---|
| 功能完整度与应用价值 | 60 | 主战场 |
| AMD Radeon GPU / ROCm 优化 | 40 | 含本地推理执行 + 推理速度优化 |

### 1.2 本项目契合点

- **tool use**：通过 MCP 调用监控 API / SSH / 配置修改 / 服务重启
- **RAG**：本地运维知识库（runbook / 调优经验 / 参数推荐）
- **workflow automation**：告警触发 -> 诊断 -> 修复闭环
- **multi-agent（逻辑多代理）**：master + 巡检/修复/question 子session
- **reasoning + planning**：ReAct 循环 + 结构化操作计划
- **memory**：DB 持久化事件历史与状态卡
- **local inference on Radeon**：llama.cpp + ROCm 本地推理

### 1.3 差异化卖点

1. **24h 无人值守 loop**：周期巡检 + 事件驱动修复，带分级审批与紧急覆盖
2. **安全护栏完整**：风险分级 + dry-run + 审批门 + 审计日志 + 回滚 + 自动熔断升级
3. **学习闭环**：解决故障后回写 runbook（置信度门控 + 人工审核）
4. **上下文工程**：一次性 context + DB 状态传递，规避长上下文推理衰减

---

## 2. 场景定义

### 2.1 目标

大数据平台（Hadoop 生态集群）的自治运维 agent：自动巡检健康状态、告警触发自动诊断与修复、历史可回溯可提问。

### 2.2 集群环境

- **3 节点 Hadoop 集群**（开源栈，弃用 Cloudera CDP 以避免授权问题并保证评委可复现）
  - 组件：HDFS（NameNode/DataNode）、YARN（ResourceManager/NodeManager）、Hive（MetaStore/Server）、HBase（Master/RegionServer）
  - 部署：docker-compose 起 3 节点，可复现
- **监控栈**：Prometheus（指标采集+告警）+ Alertmanager（告警路由+webhook）+ Grafana（可视化）
- **网络**：局域网，agent 通过工具（HTTP API / SSH）访问与操作集群

### 2.3 agent 定位

- **不替代监控平台**：监控平台负责采指标+发告警（其擅长），agent 负责**跨组件关联 + 根因解读 + 主动深检 + 修复执行**（监控做不到的）
- 例：监控报"NameNode RPC 延迟高"，agent 关联"DataNode3 心跳丢失" + 查日志 = 定位根因并修复

---

## 3. 评分拆解与策略

| 分项 | 策略 |
|---|---|
| 功能完整度 (60) | 多剧本闭环 + 安全护栏 + 24h loop + 学习回写 |
| Radeon/ROCm 优化 (40) | HIPBLAS 编译（非 Vulkan）、KV q4、FA、mmap、prompt-cache、上下文裁剪控速 |

**演示关键**：评委进不了局域网集群 -> 必须提供：① 端到端录屏 ② docker-compose 可复现环境 ③ 架构图 + README 复现步骤 ④ 性能数据（tokens/s、TTFT、VRAM、故障解决耗时）。

---

## 4. 运行环境与推理优化

### 4.1 硬件与环境（双环境：远程推理 + 本地编排）

**部署架构（已定）：远程纯推理，本地跑 Hadoop + agent + web**

- 远程：仅 llama-server（推理服务），本地通过 SSH 隧道或暴露端口调用
- 本地：Docker（Hadoop 3 节点 + Prometheus + Alertmanager + Grafana）+ agent 编排 + MCP 工具 + web UI
- 分工清晰：推理面（远程 GPU）/ 数据面+控制面（本地 CPU）

**主环境（远程 AMD 云，推理服务）**

- GPU：AMD Radeon PRO W7900D（48GB VRAM，gfx1100 / Navi 31，ROCm 官方支持）
- CPU：AMD EPYC 9334 32-Core / 128 线程
- 仅跑 llama-server，端口 8080，API key `fengfeng123`
- 存储：`/workspace` 持久卷 20G（放模型 + bootstrap.sh）；overlay 根非持久
- llama-server 二进制：`/opt/llama.cpp/llama-server`（符号链接 -> `build/bin/llama-server`）
- 连接：SSH `root@<REMOTE_IP> -p <PORT>`（安全组已放行）
- **本地访问推理 API：SSH 隧道 `http://127.0.0.1:18080` -> 远程 8080**（已验证 tool-calling 端到端通）
- 隧道命令：`ssh -o ServerAliveInterval=30 -L 18080:127.0.0.1:8080 -p <PORT> root@<REMOTE_IP> -N`
- 注：远程 jupyter-lab 为 PID 1（端口 8888），不可 kill（会重启容器）

**兜底环境（本地 7900XTX，远程不可用时退回）**

- GPU + agent + Hadoop 全部本地跑（推理+编排合体）

- GPU：AMD Radeon 7900 XTX（24GB VRAM，gfx1100 / Navi 31）
- 内存：32GB RAM
- 系统：Ubuntu 最小化安装，无图形界面；BIOS + 驱动开启 Resizable BAR / Smart Access Memory
- 资源紧张，配置需降级（见 4.7 兜底启动命令）

**两环境配置差异**

| 项 | 主（W7900D 48GB） | 兜底（7900XTX 24GB） |
|---|---|---|
| 模型 | Q4_K_M | Q4_K_M |
| KV 量化 | **q8_0**（有富余换质量） | **q4_0**（省显存） |
| 上下文 | **128k** | **32-64k**（128k 放不下：16+8+2=26GB>24GB） |
| `-ngl` | 999 全卸载 | 999 全卸载 |
| Flash Attn | `-fa on` | `-fa on` |
| 显存占用 | ~21.7GB/51.5GB | ~20-22GB/24GB（紧） |

> 兜底环境上下文上限 32-64k：与设计目标"常态 16-32k"一致，仅罕见深诊断需 128k（主环境才有）。日常巡检/修复在兜底环境可正常跑。

### 4.2 系统

- 主环境：远程云容器（Ubuntu），W7900D 为 gfx1100，ROCm 官方支持，**无需 HSA_OVERRIDE_GFX_VERSION**
- 兜底环境：本机 Ubuntu 最小化，BIOS 开 Resizable BAR
- 两环境均为 ROCm/HIP 后端，非 Vulkan

### 4.3 推理后端（已验证）

- **llama.cpp 035cd8f9a（build 9766）**，已用 **ROCm/HIP 后端**编译（`-DGGML_HIPBLAS=ON`）
- `ldd` 确认链接 `libamdhip64.so` / `libhipblas.so` / `librocblas.so` 等 ROCm 库 ✅
- **禁用 Vulkan**：赛题 40 分明确要求 ROCm，Vulkan 不计 ROCm 分
- 单一 llama-server 进程，占 GPU；其余组件全 CPU

### 4.4 模型（实测）

- 模型：`Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf`（Qwen 系 27B 稠密，支持 tool-calling ✅ 已验证）
- 路径：`/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf`
- 量化：GGUF **Q4_K_M**（权重约 16GB）
- **thinking 模型**：chat 模板 `thinking=1`，输出 `<think>` 推理。**实测 `reasoning_content` 作为独立字段返回**（不混入 content），agent 直接读字段即可，无需自行解析剥离标签
- **MTP（Multi-Token Prediction）**：GGUF 含 MTP 层，但启动日志报 `no implementations specified for speculative decoding`，即 **MTP 当前未生效**（未配 draft）。功能不受影响，仅少一份加速。属开放项，后续若启需配 speculative decoding 实现
- KV 量化升级为 **q8_0**（48GB 显存有富余，长上下文注意力误差更小，利于读日志诊断的 agent）

### 4.5 推理优化清单（实测）

| 项 | 状态 | 说明 |
|---|---|---|
| HIPBLAS（ROCm） | ✅ 已验证 | `ldd` 见 libamdhip64 等；非 Vulkan |
| KV cache 量化 | **q8_0** | `-ctk q8_0 -ctv q8_0`（48GB 有富余，从原 q4 升级换质量） |
| Flash Attention | ✅ 开 | `-fa on`（此版本需带值 on/off/auto） |
| mmap 加载 | ✅ 默认 | 无需 flag |
| Prompt caching | ✅ 已验证 | 实测 181/198 prompt tokens 命中缓存 |
| 上下文裁剪 | 必须 | 见第 8 节，控速核心 |
| 狂暴模式/超频 | 不开 | 云上无此选项，稳定性优先 |

### 4.6 性能基线（实测，Q4_K_M + KV q8 + 128k 上下文，W7900D）

**生成速度（短 prompt 长生成）**

| max_tokens | 生成 t/s | prompt t/s | TTFT |
|---|---|---|---|
| 2048 | 29.0 | 264.9 | 0.25s |
| 4096 | 29.1 | 228.5 | 0.22s |

**上下文衰减曲线（长 prompt 短生成 512 tokens）**

| 上下文 | 生成 t/s | prompt 处理 t/s | TTFT | 墙钟 |
|---|---|---|---|---|
| 4k | 28.6 | 341 | 1.5s | 8s |
| 16k | 25.9 | 213 | 71.5s | 78s |
| 32k | 22.9 | 106 | 189.8s | 199s |
| 64k | ~18-20（趋势） | ~60（趋势） | >5min | 很长 |

**VRAM 占用**：~21.7GB / 51.5GB（留 30GB 余量）

**关键发现（指导 agent 设计）**：
1. **生成速度衰减慢**：4k->32k 仅降 20%（28.6->22.9 t/s），生成不是瓶颈
2. **prompt 处理是瓶颈**：TTFT 随上下文暴涨（4k=1.5s -> 32k=190s），因 KV q8 + FA 在长 prompt 上处理变慢
3. **验证设计**：一次性 context + DB 状态传递 -> 每次新 session 从小 context 开局（TTFT 极短），不用长 prompt
4. **工具输出预压缩更重要**：大日志塞进 prompt = TTFT 灾难，必须预压缩
5. **目标**：常态工作上下文压在 **16k 以内**（TTFT < 2s，生成 ~26 t/s），32k+ 仅罕见深诊断

### 4.7 启动命令

**主环境（远程 W7900D 48GB，实测可用）**

```bash
cd /opt/llama.cpp

HIP_VISIBLE_DEVICES=0 ./llama-server \
  -m /workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf \
  -c 131072 \
  -ngl 999 \
  -ctk q8_0 -ctv q8_0 \
  -fa on \
  --jinja \
  -t 16 \
  -b 512 -ub 512 \
  -np 1 \
  --host 0.0.0.0 --port 8080 \
  --api-key "fengfeng123"
```

**兜底环境（本地 7900XTX 24GB，远程不可用时退回）**

```bash
cd /opt/llama.cpp   # 或本机 llama.cpp 路径

HIP_VISIBLE_DEVICES=0 ./llama-server \
  -m /path/to/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf \
  -c 65536 \
  -ngl 999 \
  -ctk q4_0 -ctv q4_0 \
  -fa on \
  --jinja \
  -t 8 \
  -b 512 -ub 512 \
  -np 1 \
  --host 0.0.0.0 --port 8080 \
  --api-key "fengfeng123"
```

> 兜底环境差异：KV 降 q4_0、上下文降 64k（128k 放不下 24GB）、`-t` 按本机物理核调。日常巡检/修复不受影响，仅罕见深诊断的 128k 在主环境跑。

### 4.8 重启恢复脚本

远程容器重启会丢失 overlay 层（sshd/modelscope/进程全没），`/workspace` 持久卷保留。脚本放在 `/workspace/bootstrap.sh`，重启后通过云平台 Web 终端执行：

```bash
bash /workspace/bootstrap.sh
```

脚本一键完成：① 安装+启动 sshd（恢复公钥认证）② 安装 modelscope ③ 检查模型，缺失则 `modelscope download` 下载 Q4_K_M ④ 后台启动 llama-server（nohup，日志 `/workspace/llama-server.log`）。幂等可重复执行。脚本源码见项目 `bootstrap.sh`。

---

## 5. 核心设计：24h 无人值守 + 分级审批

### 5.1 分级自治

| 等级 | 说明 | 处理 |
|---|---|---|
| 低危 | 只读（读日志/指标/状态） | 自动执行 |
| 中危 | 可回滚变更（改配置+reload、重启单个非核心服务） | 执行 + 事后通知 |
| 高危 | 影响可用性（重启 NameNode/ResourceManager、停服、扩缩容） | 暂停 + 通知 + 等审批 |
| 破坏性 | 不可逆（删数据、format、drop table） | dry-run + 必须审批 + 备份 |

### 5.2 风险判定：双层

- **静态规则白名单（权威）**：如 `restart_service('NameNode')` 规则层直接判高危，不依赖模型
- **模型建议（辅助）**：模型可提示风险，但不覆盖规则层

### 5.3 审批超时策略

```
normal:    高危 -> 等审批(10min) -> 超时 = 拒绝 + 告警
emergency: 服务 critical + 持续 > 阈值 + 未在冷却内 + 次数未超限
           -> 执行预定义剧本（仅重启该 down 服务，非自由发挥）+ 事后告警 + 计数
escalate:  紧急重启未奏效 -> 停手 + 升级人工（不无限重试）
```

要点：
- 紧急覆盖执行的是**预定义剧本**，不是模型自由操作
- 紧急自动重启**次数上限 + 冷却**（如最多 1-2 次，两次间有冷却）
- 紧急执行后**事后通知**（事后审批，不是不审批）
- "服务全挂"判定要精确（健康=critical 持续 > 阈值 且 可用性=0），规则层判定

### 5.4 审批通道

- **自建 Web 审批页**（全在 web，含其他管理员操作）
- 纯 web 无推送 = 凌晨高危审批无人看到 -> 按超时策略自动 decline / 紧急覆盖，逻辑自洽
- 可选后加：单条 webhook ping（飞书/钉钉）做"有待审批"提醒，零额外服务，链接指回 web（非 MVP 必须）

---

## 6. 系统架构总览

三层清晰：**推理面（llama.cpp）/ 数据面（Orchestrator+MCP+DB）/ 控制面（web）**。GPU 只给推理层，其余全 CPU。

```
┌─────────────────────────────────────────────────────────────────┐
│  前端  Vue3/React + Ant Design Pro                               │
│  ①审批中心 ②Agent活动台(思考链/工具调用时间线) ③集群状态(嵌Grafana) │
│  ④管理面(KB增删/监控对接/白名单风险规则)                          │
└───────────────┬──────────────────────────┬──────────────────────┘
         REST   │                    WS    │(实时:推理流/审批推送)
┌──────────────▼──────────────────────────▼──────────────────────┐
│  接入层  FastAPI + WebSocket                                      │
└───────────────┬─────────────────────────────────────────────────┘
┌───────────────▼─────────────────────────────────────────────────┐
│  编排层  Orchestrator (常驻)                                      │
│   ├─ master 调度器 (纯规则, 不耗LLM): 派发/抢占/优先级            │
│   ├─ session manager: spawn 用完即焚的子session                   │
│   └─ approval service: 风险分级/超时/紧急覆盖/计数冷却            │
└──────┬───────────────────────┬──────────────────────────────────┘
       │ HTTP(tools)           │ HTTP(chat/completions, stream)
┌──────▼──────────┐    ┌───────▼──────────────────────────────────┐
│  工具层 MCP server│    │  推理层 llama.cpp server (独立进程)        │
│  (Python SDK)    │    │  ROCm/HIPBLAS, Qwen27B Q4_K_M              │
│  Prometheus      │    │  KV q4, FA, mmap, prompt-cache             │
│  Alertmanager    │    │  占 GPU(权重16G+KV)                        │
│  SSH(白名单)     │    └───────────────────────────────────────────┘
│  CM API/HDFS     │
└──────┬───────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│  数据层  SQLite (单文件)                                          │
│   sessions / session_events / incidents / approvals / audit      │
│   + sqlite-vec (KB向量)  + bge-small(CPU编码)                     │
└──────────────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│  外部  Prometheus+Alertmanager+Grafana  ←->  Hadoop 3节点集群      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. 编排层：Orchestrator + master + 子session

### 7.1 Orchestrator（常驻进程）

```
Orchestrator (常驻)
  ├─ master 调度器 (纯规则, 不耗LLM, 持全局状态卡)
  │    ├─ 周期 spawn 巡检子session (全新context: 上次状态卡+当前指标)
  │    ├─ 告警 spawn 修复子session (抢占巡检, 全新context: 事件KB+全工具集)
  │    └─ 查询 spawn question子session (全新context: 历史库检索)
  │    每个子session: 跑完 -> 结论落DB + 摘要回master -> context释放
  ├─ session manager
  └─ approval service
```

### 7.2 master 调度器（纯规则，不调 LLM）

master 不读日志不诊断，只决策"现在派巡检还是修复？派给谁？"。用规则：

- 有告警 -> 派修复（**抢占巡检**：暂停当前巡检存状态）
- 到点（如每 5min）-> 派巡检
- 有用户查询 -> 派 question
- 多告警 -> 按 severity + 影响范围排序

纯规则 = 确定性高 + 省一次 LLM 调用 + 不占 context。只有子session 才花 LLM。

### 7.3 子session（一次性 context）

- **子session = 同一 llama.cpp server 上的独立 context window + 专属 system prompt + 工具子集**，不是独立进程、不占额外显存
- 单 GPU 无法真正并行 -> /fix 抢占 /auto，**串行执行，不同时跑**（集群出问题时巡检结果也是噪声）
- subagent 的价值是**上下文隔离**（不是并发）：/fix 读的一大堆日志不污染 /auto 上下文，反之亦然
- 用完即焚：跑完结论结构化写 DB + 回传 master 一句摘要，context 释放

### 7.4 三类子session

| 模式 | 触发 | system prompt 重点 | 工具集 |
|---|---|---|---|
| /auto 巡检 | 周期 | 跨组件关联+根因解读+主动深检 | 只读工具 |
| /fix 修复 | 告警抢占 | 诊断+修复执行 | 全工具（含高危） |
| /question 提问 | 用户查询 | 总结历史+KB | 只读+检索工具 |

---

## 8. 上下文策略：一次性 context + DB 状态传递

目标：常态 context 16-32k（≈35-40 t/s），规避长上下文衰减。比"在单个长 context 里滚动摘要"更省更稳。

### 8.1 核心机制

- **事件级隔离**：每个 incident 一个全新 context，事件之间不累积
- **子session 用完即焚**：跑完结论落 DB，下次是全新 context 从 DB 读状态开局
- **状态靠 DB 传递，不靠 prompt 记忆**

### 8.2 冷热分层（原始数据不进 LLM 上下文）

| 层 | 内容 | 位置 |
|---|---|---|
| 热 | 最近 2-3 个 ReAct 步骤 + 当前工具结果 | LLM context |
| 温 | 本事件更早步骤摘要 | 压成一段塞 context |
| 冷 | 完整原始日志/工具输出 | DB/文件，agent 按需用工具取 |

### 8.3 工具输出预压缩（省 token 大头）

- `read_logs` 不返回 5000 行原文，返回"最近 1000 行中 3 条 ERROR：[L1, L2, L3]"
- 要更多再调一次带 filter

### 8.4 滚动摘要（兜底）

- 单个子session 内若 context 到阈值（如 32k）-> 把最老 N 轮压成一段摘要，保留近期原文，循环继续

### 8.5 结构化状态存 DB

- incident 的"已试过什么/当前假设"存 SQLite JSON
- 每轮只注入一张紧凑"状态卡"，不把全历史塞 prompt

### 8.6 RAG 按需检索

- 知识库 top-k 检索注入，不预加载

---

## 9. 安全护栏

完整护栏 = 工具白名单 + 风险分级 + dry-run 预演 + 审批门 + 审计日志 + 回滚 + 自动熔断升级。

### 9.1 各机制

- **工具白名单**：SSH/HTTP 工具只允许白名单命令/端点，模型输出经结构化解析+校验后才执行，不直接喂 shell
- **风险分级**：见第 5 节
- **dry-run（预演）**：命令以无副作用模式跑，返回"会发生什么"而不真发生。如 `restart_service` dry-run 返回"将向 3 个 DataNode 发重启"。用于 agent 自验 + 人工审批前预览
- **破坏性操作确认**：不可逆操作（删数据/format/drop）即便不影响在线服务也必须审批+备份
- **审批门**：见第 5 节
- **审计日志**：每次工具调用（谁/何时/什么/结果/是否审批）写追加日志，24h 无人值守凌晨 3 点操作必须可追溯
- **回滚**：改配置前自动备份原值，能回滚
- **自动熔断升级**：agent 尝试 N 步未解决 -> 停手 + 告警人工，不无限折腾

### 9.2 紧急覆盖约束（重申）

- 执行预定义剧本（非模型自由发挥）
- 次数上限 + 冷却
- 事后通知
- 不奏效则升级人工

---

## 10. 工具层：MCP server

用 **MCP（Python 官方 SDK）** 统一封装工具。校验/白名单放 MCP server 内。好处：① agent 只调 MCP 工具，干净 ② web UI 可复用同一套工具 ③ "MCP 集成"是 Agentic AI 赛道加分词。

### 10.1 工具清单

| 类别 | 工具 | 说明 |
|---|---|---|
| 监控 | `get_metrics(metric, host, range)` | Prometheus `/api/v1/query_range` |
| 监控 | `get_alerts()` | Alertmanager API |
| 监控 | `get_cluster_health()` | 综合健康 |
| 诊断 | `read_logs(node, svc, filter, tail_n)` | **预压缩**，返回摘要行非原文 |
| 诊断 | `sql_query(db, query)` | Hive metastore，只读 |
| 诊断 | `hdfs_dfs(cmd)` | fsck/du/ls，只读子集 |
| 执行 | `run_ssh(node, cmd)` | 命令白名单+参数校验 |
| 执行 | `http_api(method, url, body)` | 端点白名单（CM API 等） |
| 执行 | `edit_remote_config(node, file, key, value)` | 备份+校验+reload，非裸编辑 |
| 执行 | `restart_service(svc)` / `start` / `stop` | 含风险分级 |
| 执行 | `exec_script(script)` | agent 生成、校验后跑 |
| 知识 | `search_kb(query)` | RAG 检索 |
| 知识 | `write_runbook(summary)` | 解决后回写，**置信度门控+人工审核**防污染 |
| 人机 | `notify(severity, msg)` | 通知 |
| 人机 | `request_approval(op, reason)` | 请求审批 |

### 10.2 write_runbook 学习闭环

差异化亮点：解决故障后回写 runbook。风险：错误经验被记住污染 KB -> 加置信度门控 + 人工审核（web 管理面审核）。

---

## 11. 监控平台对接

### 11.1 选型

**Prometheus + Alertmanager + Grafana**（弃 Zabbix：API 老旧、演示效果差）。

### 11.2 集成方式

- **Alertmanager webhook = /fix 天然触发器**：告警带 alertname/labels/severity/startsAt，结构化 payload 直接喂 agent，事件驱动入口
- **Prometheus HTTP API = MCP 工具**：`get_metrics` / `get_alerts`
- **Grafana 嵌 web UI**：iframe 嵌面板，人看指标、agent 看结论，互补

---

## 12. Web 控制台

### 12.1 定位

控制面 + 演示面。审批、管理员操作、推理过程查看、session 回看全在 web。

### 12.2 四块功能

1. **审批中心**：WebSocket 实时推送待审批项 + 一键审批
2. **Agent 活动台**：思考链 / 工具调用 / 事件历史时间线（活跃 session 经 WS 实时流，历史从 DB 回看）
3. **集群状态**：嵌 Grafana 面板
4. **管理面**：KB 增删改、监控对接配置（Prometheus 地址/组件）、白名单/风险规则配置

### 12.3 技术栈

- 后端：**FastAPI + WebSocket**（async 配 llama.cpp HTTP 完美）
- 前端：**Vue3 或 React + Ant Design Pro**（直接用 admin 模板，别从零写）
- 实时：WebSocket 推推理流 + 审批通知
- 通知（可选）：webhook 到飞书/钉钉，审批链接指回 web

---

## 13. Session 记录与回看

### 13.1 机制

- 每个子session 用完即焚（context 释放）
- 但**保存 session 调用关系 + 历史记录**用于 web 回看
- master -> 子session 的 `parent_id` 形成树，web 可下钻任一 session 看完整 ReAct 时间线

### 13.2 实时 vs 历史

- **实时看推理**：活跃 session 的 LLM 流式 token 经 WebSocket 推前端
- **历史回看**：从 `session_events` 表查

### 13.3 表结构（见第 17 节）

```
sessions(id, parent_id, type, trigger, status, summary, started_at, ended_at)
session_events(id, session_id, seq, kind, content_json, ts)
```

---

## 14. 知识库 RAG

### 14.1 用途

本地运维知识库：调优经验、参数推荐、常见故障 runbook。

### 14.2 存储

- **sqlite-vec**（SQLite 向量扩展，in-process）和事件/审计/状态卡**同一个 `.db` 文件**，零额外服务/进程
- 嵌入模型：**bge-small-zh（~100MB）跑 CPU**，不碰 GPU
- 量级：几百篇 runbook，CPU 编码足够

### 14.3 兜底

若连 100MB CPU 嵌入都嫌紧 -> 退回 **SQLite FTS5 做 BM25**（纯关键词，零额外资源）。运维 runbook 关键词为主（DataNode/OOM/GC overhead），BM25 效果也够。

---

## 15. 技术选型

### 15.1 关键认知

模型是资源大头，固定跑在 llama.cpp server（独立进程，ROCm）。orchestrator 只是发 HTTP + 跑工具 + 管状态，内存占用 ~100-300MB，相对 16GB 模型可忽略。**别为省 orchestrator 资源选语言，按开发速度+生态选。**

### 15.2 选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 编排/后端 | **Python + FastAPI** | MCP 官方 SDK、asyncio 配 llama.cpp HTTP、数据处理原生、迭代最快 |
| 前端 | Vue3/React + Ant Design Pro | admin 模板快速搭 |
| 推理 | llama.cpp（ROCm/HIPBLAS） | 赛题要求 ROCm |
| 工具协议 | MCP（Python SDK） | 统一工具层，加分 |
| DB | SQLite | 事件/审计/状态卡 + sqlite-vec KB 同文件 |
| 嵌入 | bge-small-zh（CPU） | 不占 GPU |
| 监控 | Prometheus+Alertmanager+Grafana | 大数据标配，API 友好 |

### 15.3 不上的东西

- **不上 LangGraph 等重框架**：手写 ReAct 编排器更可控、更好向评委讲清楚、更省资源
- **subagent 不上独立框架**：= 同 server 独立 context，逻辑概念，资源中性
- **DAG 不上框架**：planner 产出带依赖的操作步骤图，executor 按拓扑序跑，Python ~50 行

---

## 16. 核心数据流

### 16.1 巡检循环（周期，如 5min）

```
master 到点 -> spawn 巡检子session(全新context)
  -> 读DB上次状态卡 + MCP调 get_metrics/get_alerts
  -> LLM推理(流式token经WS推前端)
  -> 结论写DB(新状态卡+session_events) + 摘要回master
  -> context释放
```

### 16.2 告警修复（事件驱动，抢占巡检）

```
Alertmanager webhook -> Orchestrator -> master 暂停巡检(存状态)
  -> spawn 修复子session(全新context: 事件KB检索+全工具集)
  -> LLM ReAct循环: 诊断->调工具->观察->...
  -> 遇高危op: 走16.3审批
  -> 执行修复 -> 结果写DB -> 摘要回master -> 恢复巡检
```

### 16.3 审批流（含超时/紧急覆盖）

```
子session请求高危op -> approval service记DB(pending) + WS推前端
  -> 人审批 ───────────────────> 执行/拒绝, 计审计
  -> 超时(10min) -> 规则判定:
       普通高危: decline + 告警
       紧急(服务critical+未冷却+次数未超限): 执行预定义剧本 + 事后告警 + 计数
       紧急未奏效: 停手 + 升级人工
```

### 16.4 提问回看（用户驱动）

```
web发起 -> spawn question子session
  -> 查DB历史(incidents/sessions) + KB检索(sqlite-vec)
  -> LLM总结 -> 回前端 + 记session_events
```

---

## 17. 数据模型（SQLite 表结构）

```sql
-- 子session 记录
sessions(
  id            TEXT PRIMARY KEY,
  parent_id     TEXT,                 -- master 或父 session
  type          TEXT,                 -- inspect / fix / question
  trigger       TEXT,                 -- cron / alert_id / user_query
  status        TEXT,                 -- running / done / failed / aborted
  summary       TEXT,                 -- 跑完回传 master 的摘要
  started_at    INTEGER,
  ended_at      INTEGER
);

-- session 内事件（ReAct 时间线，web 回看用）
session_events(
  id            INTEGER PRIMARY KEY,
  session_id    TEXT,
  seq           INTEGER,              -- 序号
  kind          TEXT,                 -- thought / tool_call / tool_result / llm_msg / approval
  content_json  TEXT,                 -- 结构化内容
  ts            INTEGER
);

-- 事件/incident
incidents(
  id            TEXT PRIMARY KEY,
  alert_payload TEXT,                 -- Alertmanager 原始告警
  status        TEXT,                 -- active / resolved / escalated
  linked_session_ids TEXT,            -- 关联的修复 session
  resolution    TEXT,
  created_at    INTEGER,
  updated_at    INTEGER
);

-- 审批
approvals(
  id            TEXT PRIMARY KEY,
  session_id    TEXT,
  operation     TEXT,                 -- 请求的操作
  risk_level    TEXT,                 -- medium / high / destructive
  status        TEXT,                 -- pending / approved / declined / timeout_override
  requested_at  INTEGER,
  decided_at    INTEGER,
  decided_by    TEXT,                 -- user / system_timeout_override
  decision_note TEXT
);

-- 审计日志（追加）
audit(
  id            INTEGER PRIMARY KEY,
  session_id    TEXT,
  tool          TEXT,
  params_json   TEXT,
  result_json   TEXT,
  risk_level    TEXT,
  approved      INTEGER,              -- 0/1
  ts            INTEGER
);

-- 全局状态卡（master 持有的最新集群健康快照）
cluster_state(
  id            INTEGER PRIMARY KEY,
  snapshot_json TEXT,                 -- 当前健康度/活跃事件/待审批
  updated_at    INTEGER
);

-- KB runbook
runbooks(
  id            TEXT PRIMARY KEY,
  title         TEXT,
  content       TEXT,
  source        TEXT,                 -- manual / agent_generated(待审核)
  status        TEXT,                 -- approved / pending_review
  embedding     BLOB,                 -- sqlite-vec 向量
  created_at    INTEGER
);
```

---

## 18. 故障剧本（演示用）

每个剧本跑通"诊断 -> 修复"闭环。建议至少覆盖：

1. **HDFS DataNode 掉线**：心跳丢失 -> 定位 -> 重启 DataNode
2. **YARN NodeManager OOM**：GC overhead -> 定位 -> 调内存参数/重启
3. **HBase RegionServer 崩溃**：进程退出 -> 定位 -> 重启
4. **磁盘满**：日志/数据盘满 -> 清理/扩容
5. **Hive 慢查询**：查询卡住 -> 分析执行计划 -> kill/调参

每个剧本在 docker-compose 环境中可注入触发（kill 进程 / 填满磁盘 / 改坏配置），供演示与评委复现。

---

## 19. 开发顺序与里程碑

**原则：先 agent 核心闭环（console+日志验证），再包 web UI。别先做 UI 后做 agent。**

### M1 - 推理基座（先跑通模型）✅ 已完成
- [x] llama.cpp ROCm/HIPBLAS 编译验证（ldd 确认 ROCm 库）
- [x] Qwen 27B Q4_K_M + KV **q8_0** + FA(`-fa on`) + mmap + prompt-cache 跑通
- [x] tool-calling 格式验证（实测返回 `tool_calls`）
- [x] 性能基线测量：生成 26 t/s、prompt 50 t/s、VRAM 21.7GB/51.5GB
- [x] thinking 模型确认（`reasoning_content` 独立字段返回）

### M2 - 工具层 + 单 session ReAct（console）✅ 已完成
- [x] 工具层：SSH + CM API 真实实现（替代 mock），配置驱动便于切换环境
  - `get_service_status` → CM API 获取角色健康状态
  - `get_alerts` → CM API 遍历服务/角色健康检查
  - `get_metrics` → SSH 执行 free/df/top/jps
  - `read_logs` → SSH 读取远程日志，预压缩返回
  - `search_kb` → 关键词匹配（后续接入 sqlite-vec）
  - `restart_service` → CM API commands/start 启动停止的角色（✅ 已验证修复成功）
  - `hdfs_admin` → SSH 执行 dfsadmin/fsck/dfs 只读命令
- [x] 手写 ReAct 循环 + 单 session 跑通故障剧本（console+日志）
- [ ] docker-compose 3 节点 Hadoop + Prometheus + Alertmanager + Grafana
  - 当前临时使用现有 CDH 6.3.2 三节点集群（192.168.6.176/177/178）
  - 切换时只需改 config.py 的 CLUSTER_BACKEND + CLUSTER_NODES + SERVICE_MAP

### M3 - 编排层（master + 子session + 抢占）✅ 已完成
- [x] Orchestrator 常驻 + master 纯规则调度
- [x] session manager（spawn 用完即焚）
- [x] /auto 巡检周期 loop + /fix 抢占（告警驱动抢占巡检）
- [x] SQLite 落库（sessions/events/cluster_state）
- [x] 上下文策略：一次性 context + DB 状态卡传递 + 工具输出预压缩
- [x] 端到端验证（第一轮）：巡检 → 手动停 DataNode → 检测告警 → /fix 诊断修复（15 轮 ReAct, restart_service 因 JAVA_HOME 失败）
- [x] 端到端验证（第二轮）：巡检 → 手动停 NameNode → 检测告警 → /fix 诊断（查日志SIGTERM→查KB→查指标排除OOM→查jps确认进程不在）→ restart_service CM API commands/start 启动 → hdfs_admin report 验证恢复 ✅ 完整闭环

### M4 - 安全护栏 ✅ 已完成
- [x] 风险分级: 低危(自动) / 中危(执行+通知) / 高危(dry-run+审批) / 破坏性(备份+审批)
- [x] dry-run 预演: 高危操作先返回"会发生什么"而不真执行
- [x] 审批门: 高危操作记录到 approvals 表, console 模式自动批准, web 模式等人工
- [x] 审计日志: 所有工具调用写 audit_log 表 (session/tool/args/risk/status/result/ts)
- [x] 熔断升级: 连续失败 >= 3 次自动熔断, 冷却期 5min, 后续操作升级人工
- [ ] 回滚机制: 当前 restart_service 为非破坏性操作(仅启动停止角色), 回滚为 edit_remote_config 预留

### M5 - KB + 学习闭环
- [ ] sqlite-vec + bge-small(CPU) 检索
- [ ] write_runbook 回写 + 人工审核

### M6 - Web 控制台 ✅ 已完成
- [x] 后端 FastAPI: REST API (/api/sessions /api/approvals /api/audit /api/cluster/snapshot) + WebSocket (/ws 事件推送)
- [x] 事件总线 EventBus: 线程安全 queue.Queue 桥接 agent 同步代码和 WebSocket 异步推送
- [x] 前端 React + Vite + Ant Design (暗色主题):
  - 登录页 (localStorage token)
  - Sider 可收缩菜单 + Header (用户信息/通知 Badge/主题切换) + 面包屑
  - Agent 活动台: Session 树 (master→auto/fix, 时间显示, LIVE Badge) + Timeline 事件流
  - 事件渲染: Markdown (react-markdown + remark-gfm 表格) + 折叠 JSON (Collapse)
  - 审批中心: Table (pending/decided 分组, 风险标签, 通过/拒绝按钮)
  - 集群状态卡: 选中 master session 显示 Statistic + Descriptions 服务状态
- [x] 流式输出: llm_client.chat_stream (SSE) + agent on_chunk 回调逐 token 推送到 WebSocket
  - 前端实时拼接: stream_reasoning/stream_content 增量追加, 完整事件替换流式内容
  - 首字延迟正常 (推理模型思考阶段)
- [x] 智能滚动: scrollRef + atBottomRef, 用户在底部才自动滚动, 向上查看历史不打断
- [x] 全中文 UI
- [ ] 集群状态面板 (嵌 Grafana, 待 docker 环境搭建后接入)
- [ ] 前端 admin 模板进一步美化

### M7 - 演示与提交

> 清单见 `docs/TODO.md` T9（含多故障剧本 / 录屏 / README 复现 / 性能数据）。

---

## 20. 开放问题（设计遗留决策）

> 实施类待办已统一移至 `docs/TODO.md`（含 §21 实施步骤、M7 演示清单、待敲定参数）。本节仅保留**设计层面的开放决策**，不再重复待办。

| 项 | 状态 | 备注 |
|---|---|---|
| MTP 是否被 llama.cpp 支持 | ✅ 已启用 | `--spec-type draft-mtp --spec-draft-n-max 1`。基准测试 (n_max=1~8)：n_max=1 最优 37.5 t/s (+30% vs baseline 28.9 t/s)，接受率 77.4%。n_max 越大接受率衰减越快 (n_max=8 仅 24%)，最优值为 1 |
| 模型确切型号与官方 GGUF | ✅ 已确认 | `Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf`，27B 稠密 + tool-calling 可用 |
| thinking 模型处理 | ✅ 已确认 | `reasoning_content` 独立字段，agent 直接读，无需解析 `<think>` |
| 紧急覆盖阈值/次数/冷却 | ✅ 已由 §21 解决 | 改为 attempt 节流（按 `(tool,target)` 查 audit_log，超限升级人工）；具体数值见 TODO 待敲定 |
| 通知 webhook（可选） | 待定 | MVP 可不做，后加单条 ping（见 TODO 待敲定） |
| KB 检索最终用向量还是 BM25 | 待定 | M5 待办（见 TODO） |
| 审批超时 / 巡检周期 / `-t` 线程 | 暂定 | 10min / 5min / 16，均可调（见 TODO 待敲定） |
| restart_service 启动失败 | ✅ 已修复 | 改用 CM API commands/start，通过 CM 管道管理，不依赖 JAVA_HOME |
| 集群环境 | 临时 CDH → docker | 当前用 CDH 6.3.2 三节点（176/177/178），后续切 docker-compose（见 TODO 其他遗留） |
| CM API 单角色操作 | ❌ 不支持 | CM API v30 仅支持 commands/restart（整个服务）；recover 档按服务单位执行 |

---

## 21. 安全护栏与分级自治 — 最终实现方案（已落定）

> 本节为 §5 / §9 的最终落地方案，替代原 §5.3 紧急覆盖的模糊描述。
> 设计评审结论：定级权归规则不归模型；规则 DB 化、页面可配；attempt 节流覆盖所有高危。

### 21.1 设计原则

- **定级权归规则，不归模型**：模型若参与定级，可能把不可逆操作判成"低风险自动执行"。风险等级必须由模型够不着的规则决定（呼应 §5.2）。
- **工具白名单天然可分类**：agent 只能调 `TOOL_DEFINITIONS` 注册的工具，没有裸 shell。每个动作都能确定性映射到某一档，无需模型"理解意图"。
- **fail-closed**：任何不在白名单 / 无匹配规则的工具，一律按 `irreversible` + 不可自动处理，绝不放过。
- **模型的合法角色仅两项**：① 选哪个工具（tool-use reasoning，已在做）；② 给出理由文字（写进审计 / 给人看）。理由**不参与定级**。

### 21.2 双轴模型

| 轴 | 取值 | 含义 |
|---|---|---|
| 轴1 自治模式 `AUTONOMY` | `supervised` / `autonomous` | **谁来决策**：值守（等 Web 人工）还是无人值守（策略自动） |
| 轴2 操作自治等级 `tier` | `recover` / `reversible` / `irreversible` / `low` / `medium` | **策略怎么动** |

四档在 `autonomous` 下的行为：

| 等级 | 典型操作 | autonomous 行为 | supervised 行为 |
|---|---|---|---|
| `low` / `medium` | 只读 / 重启非核心 | 维持现状（自动 / 执行+通知） | 维持现状 |
| `recover`（可恢复幂等） | 重启已 `DOWN`/`STOPPED` 的服务 | 自动执行，受 attempt 节流（重试上限+冷却），连续失败升级人工 | 等人工审批 |
| `reversible`（可回撤） | 改配置前先备份→改→重启 | 自动执行，强制先备份留回滚点，仍写审计 | 等人工审批 |
| `irreversible`（不可逆） | `hdfs format` / `disk format` / `rm` 关键文件 / `drop table` | **永不自动**：直接放弃本次操作 + 发升级告警 | 等人工审批；超时=拒绝 |

### 21.3 定级：纯规则 + DB 支撑 + 页面可配

定级是**确定性纯函数**，由两层规则组成，均不调模型：

```
classify(tool_name, args) -> tier, autonomous:
    1) 查 risk_rules 表（带 TTL 缓存）：
       取 enabled 且 (tool_name==name 且 match_json 命中 args) 中 priority 最高者
    2) 若无则取 tool_name=='*' 的默认规则
    3) 若仍无 → 代码兜底 (tier=irreversible, autonomous=False)  # fail-closed
    4) 运行时精炼（仍是规则，读 CM 实时状态）：
       if tool == restart_service:
           state = cm_role_state(args.service, args.node)
           if state in {STOPPED, DOWN, UNKNOWN}: 维持 recover
           else (RUNNING 但不健康): 降为等人工 (irreversible 流程)
    5) 返回 (tier, autonomous)
```

**`risk_rules` 表（定级权威来源，页面可增删改）**

```sql
CREATE TABLE risk_rules (
  id          TEXT PRIMARY KEY,
  tool_name   TEXT NOT NULL,   -- 匹配工具名; '*' 表示默认
  match_json  TEXT,            -- 可选: 按 args 细分 (如 action=='format'->irreversible), NULL=任意
  tier        TEXT NOT NULL,   -- recover|reversible|irreversible|low|medium
  autonomous  INTEGER NOT NULL DEFAULT 0,  -- autonomous 模式下是否允许自动执行
  enabled     INTEGER NOT NULL DEFAULT 1,
  priority    INTEGER NOT NULL DEFAULT 0,   -- 同工具多条规则取最高
  updated_at  INTEGER,
  updated_by  TEXT
);
```

- **种子数据**：首次启动若表空，从现有 `TOOL_RISK`（`tools.py`）灌默认规则，开箱即用。
- **缓存**：`classify` 查库带 TTL 缓存（同告警缓存机制），避免每次工具调用打 DB。
- **UI 护栏**：`irreversible` 档在管理页面**禁止**把 `autonomous` 勾成 1（代码强制），防管理员手滑。
- **fail-closed 兜底**保留在代码常量，DB 规则缺失时生效。

### 21.4 各档执行细节

- **`recover`**：仅当 `roleState ∈ {STOPPED, DOWN, UNKNOWN}` 才自动重启（已挂，重启不会更糟）；若服务 `RUNNING` 但不健康（如 GC overhead / 假死），**不主动制造中断**，转人工或仅通知。重启走 CM `commands/start`（整服务，见 §20 限制），受 §21.5 attempt 节流。
- **`reversible`**：执行前 `cp file file.bak.<ts>`，改完 reload/重启；回滚点可追溯。工具 `edit_remote_config`（§21.7 实现）落地此档。
- **`irreversible`**：永不自动。supervised 等审批；autonomous 直接放弃 + 升级告警（不傻等超时）。
- **`low`/`medium`**：维持 §9 现状（自动 / 执行+通知）。

### 21.5 高危尝试节流（覆盖 recover + reversible）

原熔断只数**失败**（`_failure_counts`）。新增**尝试节流**，覆盖所有高危档的**每次 autonomous 执行**（成功也算）：

- **键**：`(tool, target)`，target = service / node / path（按工具取）。
- **计数派生自 `audit_log`**（无需新状态，天然持久化、跨 session / 重启不丢）：

```sql
SELECT COUNT(*) FROM audit_log
 WHERE tool_name=? AND json_extract(args_json, '$.service')=? AND status='executed'
   AND ts > now - WINDOW;          -- WINDOW 为观察窗口
```

- **冷却**：两次 autonomous 执行间隔 < `cooldown` 则拒绝（取上次执行 `ts`）。
- **超限升级**：`attempts >= MAX_ATTEMPTS` → 标记 escalated、停止该键的 autonomous 自动执行、发升级告警。
- 与 §9 熔断互补：熔断管"一直失败"，attempt 节流管"试了 N 次还不成就放弃"。

### 21.6 与现有机制关系

- `AUTO_APPROVE`（`config.py`）演进为 `AUTONOMY` 模式（supervised/autonomous），语义更清晰。
- `Guardrail` 现有 `auto_approve` 参数 = `AUTONOMY=='supervised'` 时 False。
- `RISK_DESTRUCTIVE` 在 `tools.py`/`guardrails.py` 已定义却从未使用 → 本方案正式接入为 `irreversible` 档。
- 现有熔断（`max_failures`/`cooldown`）保留作失败侧；attempt 节流作尝试侧。

### 21.7 实施步骤

> 详细可勾选清单见 `docs/TODO.md`（T1–T8）。

| 步 | 内容 |
|---|---|
| T1 | `risk_rules` 表 + 迁移 + 种子（从 `TOOL_RISK` 灌默认） |
| T2 | `classify()`：查库(缓存) + fail-closed + `match_json` 命中 |
| T3 | `Guardrail` 四档分支 + `AUTONOMY` 轴接入 |
| T4 | attempt 节流：按 `(tool,target)` 查 `audit_log` 计数 + 冷却 + 超限升级 |
| T5 | `reversible` 落地：`edit_remote_config`（先 `cp .bak` 再改再 reload） |
| T6 | `config`：`AUTONOMY` 模式替换 `AUTO_APPROVE` |
| T7 | Web API：`risk_rules` CRUD + 管理页面 |
| T8 | 联调：无人值守端到端（DOWN 自动重启重试 / irreversible 拒绝升级） |

---

## 附录：关键约束备忘

- **双环境：主=远程 AMD 云 W7900D 48GB + EPYC 9334 128 线程；兜底=本地 7900XTX 24GB + 32GB RAM**
- 主环境 VRAM 21.7GB/51.5GB 余量足，KV q8_0；兜底 24GB 紧，KV q4_0 + 上下文降 64k
- GPU 只给 llama-server，其余组件全 CPU
- 单 GPU 无法真并行 -> /fix 抢占 /auto，串行（-np 1）
- 常态上下文目标 16-32k，128k 仅罕见深诊断（仅主环境支持）
- 稳定性 > 微小提速（不开超频）
- thinking 模型：`reasoning_content` 独立字段，agent 直接读取
- 可复现性：docker-compose + 录屏 + README（评委进不了局域网）
- 主环境持久存储：`/workspace` 20G 持久卷（放模型），overlay 根非持久
