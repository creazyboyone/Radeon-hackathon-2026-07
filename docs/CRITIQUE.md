# 项目全面审查报告

> 生成时间：2026-07-18  
> 审查对象：AMD AI DevMaster Hackathon 2026 — AIOps Agent（Track 2 Agentic AI）  
> 审查范围：架构 · 代码 · 安全 · 合规 · 交互 · 部署 · 与商业产品差距

---

## ✅ 已修复（2026-07-18 首轮）

以下条目已在代码中修复并提交，详见对应章节的核实说明：

| 条目 | 内容 | 提交主题 |
|------|------|---------|
| 1.1 | `app.py` `from .config` 导入错误（启动即崩，已实测复现） | `feat: M5 知识库` |
| 1.2 | runbook 编辑后 embedding 置 `b""` 导致永不重编码且检索消失 → 改 `None` | `feat: M5 知识库` |
| 3.1 | `cosine_similarity` 纯 Python → numpy 向量化 | `fix: kb 向量检索` |
| 3.2 | BM25 归一化 `1.0+score` 在 score<-1 时最相关结果垫底 → 保序映射 | `fix: kb 向量检索` |
| 1.3 | 熔断器类级 dict 无锁竞态 → 加 `_circuit_lock` | `fix: 并发安全` |
| 2.6 | 告警缓存 check-then-set 非原子 → 加 `_alerts_cache_lock` | `fix: 并发安全` |
| 4.1 | `fengfeng123` 明文 key + SSH 公钥硬编码 → 环境变量注入 | `security: API key` |

**核实修正**：1.4（学习循环重复触发）与 3.5（`inject_fault`）经代码核实后判断偏差，已在正文更新说明，均无需改代码。

---

## 目录

1. [关键 Bug（必须修复）](#1-关键-bug必须修复)
2. [架构与设计层面](#2-架构与设计层面)
3. [代码质量层面](#3-代码质量层面)
4. [安全层面](#4-安全层面)
5. [比赛合规层面](#5-比赛合规层面)
6. [用户交互与易用性层面](#6-用户交互与易用性层面)
7. [部署层面](#7-部署层面)
8. [与商业 AIOps 产品的差距](#8-与商业-aiops-产品的差距)
9. [可借鉴的特性清单](#9-可借鉴的特性清单)
10. [优先级汇总](#10-优先级汇总)

---

## 1. 关键 Bug（必须修复）

### 1.1 `app.py` 启动即崩溃 ⛔

**文件：** `src/web/app.py:17`  
**问题：** `from .config import CONSOLE_TOKEN`——`src/web/` 子包内没有 `config.py`，启动时直接 `ImportError`。  
**修复：**
```python
# 改为
from src.config import CONSOLE_TOKEN
```

### 1.2 Runbook 嵌入向量清空后永不重建

**文件：** `src/web/app.py`（`update_runbook` 路由）  
**问题：** 内容变更时调用 `store.update_runbook_embedding(rb_id, b"")`（空字节串），而 `get_runbooks_for_embedding` 的查询条件是 `embedding IS NULL`，空字节串不等于 NULL，导致被编辑的 runbook 向量永远不会被重新生成，混合搜索结果陈旧。  
**修复：** 改为 `store.update_runbook_embedding(rb_id, None)` 或在 SQL 中显式写 `UPDATE ... SET embedding = NULL`。

### 1.3 Circuit Breaker 非线程安全

**文件：** `src/guardrails.py`  
**问题：** `_failure_counts` 和 `_last_failure_ts` 是类级别 `dict`，在多会话并发场景下多个线程同时读写，存在竞态条件（check-then-set 非原子）。  
**修复：** 用 `threading.Lock` 包裹读写，或改用 `threading.local` + 实例级锁。

### 1.4 M5 学习循环重复触发（经核实：风险极低，暂不改）

**文件：** `src/agent.py`（`run()` 末尾，`_session_used_tool`）  
**核实结论：** `_session_used_tool` 查询 `kind='tool_call'` 事件，而 tool_call 事件在工具执行**前**就写入（`agent.py:156`，先 `log_event` 再 `execute`）。因此只要 Agent 发起过 `write_runbook` 调用即被记录，不存在"事件部分失败导致漏判"——除非 `log_event` 本身抛异常，但那样整个 ReAct 循环都会中断。**原判断偏保守，实际重复触发风险极低，本轮不改代码。**  
**可选优化（低优先级）：** 若追求极致健壮，可在 `ReActAgent` 实例维护 `_wrote_runbook: bool` 标志，语义比查表更直接。

---

## 2. 架构与设计层面

### 2.1 Orchestrator 主线程阻塞整个进程

**问题：** `main.py` 将 `Orchestrator.run()` 跑在主线程，FastAPI/uvicorn 以 daemon 线程跑。若 Orchestrator 因未捕获异常退出，整个进程终止，Web 控制台跟着挂。反之，若 Web 请求（如触发审批）长时间阻塞 uvicorn 线程池，也会影响 Orchestrator 的轮询间隔。  
**建议：** 将 Orchestrator 移到独立的非 daemon 线程，主线程只负责生命周期管理；对 Orchestrator 加 `try/except Exception` + 重试回退，防止单次异常终止整个调度循环。

### 2.2 `supervised` 模式下审批阻塞 Agent 线程长达 10 分钟

**问题：** `_request_approval` 在 `guardrails.py` 中以每 2 秒轮询 SQLite 的方式等待人工审批，最长阻塞 600 秒。此期间 Orchestrator 调度线程完全冻结，不能响应新告警、不能执行新的 auto 巡检。  
**建议：** 将审批等待改为异步回调模型——Agent 挂起后立即返回"等待审批"状态，审批通过后由 WebSocket 事件或消息队列唤醒继续执行。或对单次审批超时缩短至 5 分钟，并在超时后自动降级为 `reject`（已标记超时原因）。

### 2.3 EventBus 存在内存泄漏风险

**问题：** `EventBus` 用 `set` 维护队列引用，`subscribe()` 只在正常 WebSocket 关闭时调用 `unsubscribe()`。若客户端异常断开（网络抖动、浏览器强关），队列留在 set 里永不清除，事件持续堆积。  
**建议：** 在 WebSocket 处理器的 `finally` 块中保证调用 `bus.unsubscribe(q)`；或给队列加 TTL，定期清理长时间无消费者的孤儿队列。

### 2.4 单一 SQLite 文件承担所有并发读写

**问题：** WAL 模式可以支持多读单写，但所有表（sessions、audit_log、approvals、runbooks）都挤在同一个 `aiops.db`，在多线程高频写入审计日志时会产生写锁竞争，尤其是 `with store.lock:` 的 `RLock` 串行化了所有操作。  
**建议：** 短期内可将高频写入（audit_log、session_events）和低频配置表（risk_rules、runbooks）分拆为两个 DB 文件，减少锁竞争。长期考虑迁移到轻量关系型 DB（如 PostgreSQL via Docker）。

### 2.5 Agent 工具注册机制缺乏模式校验

**问题：** `@tool(name)` 装饰器仅注册函数，工具的参数 schema 是硬编码在 `agent.py` 的 `TOOLS` 列表里，与实际函数签名没有自动同步机制。若某工具函数增减参数，schema 和实现很容易产生漂移。  
**建议：** 借鉴 LangChain/Pydantic 工具模式——用 `@tool` 装饰器从函数的类型注解和 docstring 自动生成 JSON Schema，或至少加一个启动时校验步骤，对比 TOOLS schema 和函数签名是否一致。

### 2.6 `get_pending_alerts` 缓存非线程安全

**问题：** `src/tools.py` 中模块级的 `_alert_cache / _alert_ts` 字典，在 Orchestrator 线程和可能的 Web API 线程并发访问时，`if now - _alert_ts[...] > TTL` 后的赋值不是原子操作。  
**建议：** 用 `threading.Lock` 包裹整个缓存读写逻辑，或使用 `functools.lru_cache` + `threading.local`。

### 2.7 `Retry(total=0)` 实际等于无重试

**问题：** `tools.py` 中 CM API 的 `requests.Session` 设置了 `Retry(total=0)`，等同于没有设置 Retry，任何网络抖动都会直接抛出异常传播到 Agent，触发 ReAct 迭代消耗。  
**建议：** 至少设置 `Retry(total=2, backoff_factor=0.5, status_forcelist=[502,503,504])` 处理瞬态网络问题，同时保留 3s/5s 的 connect/read timeout 作为上限。

### 2.8 Orchestrator 不支持并行 Fix 会话

**问题：** 当前 Orchestrator 串行处理告警——前一个 fix 会话结束后才处理下一个告警。真实集群可能同时多个服务告警，串行处理会让后续告警等待时间过长。  
**建议：** 允许同类型服务的 fix 会话并行（用线程池或 asyncio），对同一服务的操作保持串行（用服务粒度锁）。

---

## 3. 代码质量层面

### 3.1 向量相似度用纯 Python 实现，性能极差

**文件：** `src/kb.py`  
**问题：** `cosine_similarity` 用 Python 列表循环计算点积，512 维向量每次 O(512) 循环，有 N 条 runbook 就做 N 次。即使现在 runbook 少，随知识库扩张性能会崩。  
**建议：** 用 `numpy.dot` 替换（只需 `import numpy as np`，`sentence-transformers` 已经依赖 numpy），性能提升 100x+：
```python
import numpy as np
def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
```

### 3.2 BM25 分数归一化逻辑有符号错误风险

**文件：** `src/kb.py`  
**问题：** FTS5 的 BM25 分数是负数（越接近 0 越好）。代码用 `1.0 + bm25_score` 做归一化，当 BM25 分数 < -1.0 时，归一化分数变为负数，与向量分数合并后可能将高相关 runbook 排到低位。  
**建议：** 用 `max(0.0, 1.0 + bm25_score / max_abs_score)` 做归一化，或直接使用 rank 倒数融合（RRF），更稳健。

### 3.3 `edit_remote_config` 的 Python 替换方式不可靠

**文件：** `src/tools.py`  
**问题：** 通过 SSH 执行 `python3 -c "content=open(...).read(); content=content.replace(...); open(...,'w').write(content)"` 进行配置替换。若 `find` 字符串中含有 Python 语法特殊字符（引号、反斜杠等），`shlex.quote` 的外层保护可能无法完全防止注入；更重要的是替换后没有对 XML/Java Properties 做语法校验，直接重启服务可能导致配置破坏。  
**建议：** 加入替换后的配置语法校验步骤（如 `xmllint --noout` 检查 XML 配置），并在写入前 diff 预览变更内容，记录到 audit_log。

### 3.4 `MAX_REACT_ITERATIONS=15` 与 `MAX_TOKENS=2048` 组合可能超出模型上下文

**问题：** 每轮 ReAct 迭代最多输出 2048 token，15 轮最多 30720 token 输出，加上系统 prompt 和工具调用历史，累积 token 数可能接近或超过 128k 上限，导致后期迭代截断。  
**建议：** 在 `agent.py` 中追踪已消耗 token（`usage` 字段已从 LLM 返回），当剩余上下文窗口 < 某阈值时提前结束 ReAct 循环并生成摘要；或对历史消息实施滑动窗口压缩。

### 3.5 `inject_fault` 是未使用的占位函数（经核实：非工具，不影响 Agent）

**文件：** `src/tools.py:820`  
**核实结论：** `inject_fault` **没有** `@tool` 装饰器，不在 `TOOL_HANDLERS` 中，`agent.py` 的工具列表（`AUTO_TOOL_NAMES` / `FIX_TOOL_NAMES`）也未引用它——**Agent 根本调不到，是纯死代码/占位符，不会浪费迭代或混淆推理**（原判断"注册为工具"不准确）。  
**建议：** 迁移到 Apache Hadoop docker-compose 环境时，将其实现为 `docker stop/start <container>` 的故障注入，供一键 Demo 使用（见 [ROADMAP.md](./ROADMAP.md) §4.5）；在此之前保留占位无害。

### 3.6 系统 Prompt 全中文，工具 hint 也是中文

**问题：** Qwen-27B 虽然中文能力强，但工具调用（JSON 格式输出）方面英文 prompt 通常比中文更稳定，尤其是工具参数的 JSON key 和值的格式。  
**建议：** 将工具 schema 的 `description` 改为中英双语或纯英文，系统 prompt 中的工具调用格式示例部分改为英文，正文保持中文，以减少格式错误率。

### 3.7 日志和打印混用

**问题：** 项目大量使用 `print()` 输出运行信息（agent.py、orchestrator.py、tools.py 等），没有统一的 logging 框架，无法按级别过滤，生产调试困难。  
**建议：** 引入标准 `logging` 模块，配置 `structlog` 或自定义 JSON 格式，统一日志级别（DEBUG/INFO/WARNING/ERROR），为 Web 控制台提供日志流接口。

---

## 4. 安全层面

### 4.1 明文密钥硬编码在 bootstrap.sh 且已提交到仓库

**文件：** `scripts/bootstrap.sh`  
**问题：** `API_KEY=fengfeng123` 和 SSH 公钥 `AAAAC3NzaC1lZDI1NTE5...` 明文提交到 git 历史，即使后续删除文件，git 历史中仍可查。bench 脚本也硬编码了同一 API key。  
**严重性：** 中高（黑客赛道评委可能检查仓库安全性；AMD 远程服务器存在被接管风险）  
**修复：** 立即 rotate API key；用 `git filter-repo` 清洗 git 历史；改用环境变量或 `secrets_local.py`（已 gitignore）注入 key。

### 4.2 Web 控制台默认无鉴权

**问题：** `CONSOLE_TOKEN=""` 时，auth middleware 直接放行所有请求，任何能访问 8000 端口的人都可以调用 `approve/reject`、修改 `risk_rules`、删除 `runbooks`。  
**建议：** 哪怕 hackathon demo 阶段，也应默认生成一个随机 token 并打印到启动日志，而不是默认空（无鉴权）。

### 4.3 `edit_remote_config` 和 SSH 命令潜在注入面

**文件：** `src/tools.py`  
**问题：** `hdfs_admin` 的 `path` 参数虽然有基本校验（不能含 `..`，必须以 `/` 开头），但没有长度限制和字符白名单；SSH 命令通过 `subprocess.run` + `shlex.quote` 组合，整体安全，但多层字符串拼接（尤其是 `edit_remote_config` 的 find/replace 内容）仍需更严格的 schema 校验。  
**建议：** 对所有工具入参加 Pydantic 校验（类型、长度、正则），在 guardrail 层做二次校验，拦截超长或含特殊字符的参数。

### 4.4 API 端点无速率限制

**问题：** FastAPI 端点没有速率限制，`/api/approvals` 的 `decide` 接口可被重放，`/api/runbooks` 可被暴力爬取。  
**建议：** 使用 `slowapi`（基于 limits）添加速率限制中间件，对写操作限制更严格（如 10 req/min）。

### 4.5 CORS 配置在生产环境过于宽松

**问题：** `allow_origins=["http://localhost:3000","http://localhost:5173"]` 在开发时合理，但若服务部署到 AMD 远程服务器并开放公网访问，应改为具体的生产域名。  
**建议：** 将 CORS origins 提取到配置文件，生产环境强制要求显式设置。

---

## 5. 比赛合规层面

### 5.1 M7（Demo + 提交）尚未完成，距截止日期约 19 天

**风险：** `docs/TODO.md` 中 M7 和 Docker-compose 3 节点 Hadoop 环境均为 Pending。视频录制、性能数据整理、Docker 环境验证是评分的重要组成部分，需要优先推进。  
**建议：**
- 立即开始录制 demo 视频（哪怕草稿版），对照评分标准逐项确认展示内容
- Docker-compose 3 节点 Hadoop 环境的构建应是下一个最高优先级技术任务

### 5.2 AMD Radeon GPU 使用展示不够突出

**问题：** README 提及了 W7900D + ROCm/HIPBLAS + MTP 投机解码（37.5 t/s），但评委能看到的只是文字描述，缺少：
- GPU 利用率截图（`radeontop` 或 `rocm-smi` 输出）
- 推理性能对比图（基准测试结果可视化）
- AMD 特有优化（ROCm、HIPBLAS、Flash Attention、KV cache 量化）的说明

**建议：** 在 README 和 demo 视频中专门用一节展示 GPU 监控面板和推理性能数据，强调 AMD Radeon 而非通用 GPU。

### 5.3 项目名称/README 与比赛主题的对齐度

**问题：** README 的核心叙述是"大数据集群 AIOps"，强调 CDH/Cloudera Manager，这与 Agentic AI track 的评分重点（agent 能力、工具使用、自主性）的对齐度需要加强。  
**建议：** 在 README 开头增加一节"Agentic AI 特性展示"，明确列出：工具调用数量、ReAct 循环示例、guardrail 四层决策示意图、知识库学习循环流程图。

### 5.4 英文 README 质量需提升

**问题：** `docs/README_EN.md` 是中文版的直译，部分表达不够自然，缺少对评委友好的摘要（TL;DR）和架构图。  
**建议：** 增加一份架构图（Mermaid 或 ASCII），添加 Quick Start 章节，补充关键技术决策的英文说明。

### 5.5 缺少可复现的 Demo 脚本

**问题：** 评委无法独立复现 demo——没有 `demo.sh` 或 `demo.py`，没有模拟告警的触发方式，`inject_fault` 是空函数。  
**建议：** 提供一个 `scripts/demo.sh`，自动：启动服务→注入模拟告警→触发 fix 会话→展示 Web 控制台，让评委或自动化 CI 可以一键运行。

---

## 6. 用户交互与易用性层面

### 6.1 无集群健康总览仪表板

**问题：** Web 控制台缺少一个"首页"或"总览"页面，用户进入后直接看到 Agent Activity（时间线），没有当前集群状态的一眼可见摘要（服务状态、告警数、最近事件）。  
**建议：** 新增 Dashboard 组件，展示：服务状态卡片（绿/黄/红）、活跃告警计数、最近 5 次 fix 操作、当前 autonomy 模式徽章。

### 6.2 无法从 UI 中止运行中的 Agent 会话

**问题：** 一旦 fix 会话启动，无法从 Web 控制台中止它，只能重启进程。这在 Agent 进入错误推理循环时尤其危险。  
**建议：** 增加"终止会话"按钮，后端在 `sessions` 表设置 `status=cancelled`，agent run 循环在每轮迭代开始时检查此标志并提前退出。

### 6.3 审批中心缺少实时推送通知

**问题：** 需要人工审批的操作出现时，用户必须主动切换到 ApprovalCenter 标签页才能看到，没有任何主动通知（弹窗、浏览器通知、声音提示）。  
**建议：** 利用已有 WebSocket，在新审批请求到来时，推送一个浏览器 Notification（`Notification API`）或在页面顶部显示 Ant Design `notification.warning()`；同时给 Sider 菜单的审批项加上数字徽章（`Badge`）。

### 6.4 Agent 活动流缺少进度提示

**问题：** ReAct 循环最多 15 步，但 UI 只显示已发生的事件流，用户不知道当前是第几步、还有多少步、预计还需多久。  
**建议：** 在会话卡片头部显示"迭代 N/15"和已耗时，或在活动流顶部加进度条。

### 6.5 工具调用结果 JSON 展示不够友好

**问题：** 工具调用结果以折叠 JSON 展示，对于 `read_logs` 返回的多行日志，用户难以快速扫描关键错误。  
**建议：** 对 `read_logs` 结果增加特殊渲染：错误行高亮红色、警告行高亮黄色，关键字匹配加粗；对 `get_metrics` 结果用简单的进度条或数值卡片替代原始 JSON。

### 6.6 Risk Rules 编辑器缺乏引导

**问题：** `match_json` 字段是个原始文本框，用户不知道填什么格式，很容易填错导致规则不生效（且没有校验反馈）。  
**建议：** 添加内联帮助文本和示例（如 `{"service": "NameNode"}`），前端在提交前做 JSON 格式校验并即时显示错误。

### 6.7 知识库搜索测试入口太隐蔽

**问题：** KnowledgeBase 组件有搜索测试功能，但入口不明显，且搜索结果只显示标题，没有内容预览、相关度分数、匹配高亮。  
**建议：** 将搜索框提升为首要元素，结果卡片显示内容摘要（前 100 字）、分数（向量相似度 + BM25）、匹配关键词高亮。

### 6.8 无暗色模式

**问题：** `App.tsx` 提到 theme toggle，但运维人员通常在暗环境长时间使用控制台，亮色界面眼疲劳。  
**建议：** 实现 Ant Design 5 的 dark algorithm token（`theme={{ algorithm: theme.darkAlgorithm }}`），用 localStorage 持久化偏好。

### 6.9 长会话历史无分页/虚拟列表

**问题：** AgentActivity 组件加载所有 session_events 到 DOM，若 events 超过几百条（长时间运行后），DOM 节点膨胀导致页面卡顿。  
**建议：** 使用 `react-virtual` 或 Ant Design Virtual List 实现虚拟滚动，或对历史事件实施后端分页。

---

## 7. 部署层面

### 7.1 Docker-compose 3 节点 Hadoop 环境缺失（阻塞 Demo）

**问题：** 当前依赖真实 CDH 集群，评委无法独立复现环境。M7 中这是明确的 Pending 项。  
**建议：** 使用 `big-data-europe/docker-hadoop` 或 `gchq/gaffer-docker` 基础镜像搭建 3 节点 Hadoop（NameNode + 2 DataNode），配合 docker-compose 一键启动。CM API 部分可用 mock server 或直接改用 JMX/REST API。

### 7.2 `requirements.txt` 使用非固定版本

**问题：** `>=` 版本范围在不同时间安装可能得到不同版本，破坏复现性。  
**建议：** 用 `pip freeze > requirements.lock.txt` 生成固定版本文件，或迁移到 `pyproject.toml` + `uv lock`。

### 7.3 缺少控制平面的 Dockerfile

**问题：** 只有 `bootstrap.sh`（针对远程 GPU 服务器），没有控制平面（Python 后端 + React 前端）的 Dockerfile。  
**建议：** 添加：
```
Dockerfile           # Python backend
web/Dockerfile       # React frontend (nginx)
docker-compose.yml   # 整体编排
```

### 7.4 前端构建产物未集成到后端

**问题：** React 前端和 FastAPI 后端是分离运行的（前端 dev server on :5173，后端 :8000），生产部署需要将 `web/dist` 挂载到 FastAPI 的 `StaticFiles`，但当前代码没有这个集成。  
**建议：** 在 `app.py` 增加 `app.mount("/", StaticFiles(directory="web/dist", html=True))`，并在 Dockerfile 里先 build 前端再运行后端。

### 7.5 无健康检查端点

**问题：** 没有 `/health` 或 `/readyz` 端点，Docker healthcheck、K8s liveness probe 无法工作。  
**建议：** 添加：
```python
@app.get("/health")
def health():
    return {"status": "ok", "llm_reachable": llm_client.ping()}
```

### 7.6 SSH 隧道是单点故障

**问题：** LLM 推理依赖 `localhost:18080` 的 SSH 隧道，若隧道断开，Agent 所有 LLM 调用立即失败，但没有自动重连机制或降级策略（如本地小模型兜底）。  
**建议：** 在 `LLMClient` 中增加连接探活（ping `/v1/models`），断连时触发告警并暂停 Orchestrator 调度，而不是让 LLM 调用静默失败传播到 Agent 层。

### 7.7 前端无环境配置机制

**问题：** React 前端的 API base URL 硬编码为 `http://localhost:8000`（推测），没有通过 `.env` 或构建时注入配置，部署到不同环境需要修改源码。  
**建议：** 使用 Vite 的 `import.meta.env.VITE_API_URL` 环境变量机制。

---

## 8. 与商业 AIOps 产品的差距

以下对比参考 Datadog AIOps、Dynatrace Davis、PagerDuty AIOps、Splunk ITSI。

| 能力维度 | 本项目 | 商业产品 | 差距说明 |
|---------|--------|---------|---------|
| **根因分析（RCA）** | Agent 通过 ReAct 推理给出原因 | ML 自动拓扑分析，置信度评分，因果链可视化 | 缺少置信度评分和可视化因果链 |
| **告警去重与关联** | 直接处理所有 CM 告警 | 基于时间窗口和拓扑的告警聚合，降噪 80%+ | 没有告警聚合，多告警可能触发重复 fix |
| **异常检测** | 仅靠 CM health check（规则阈值） | 基线 ML（动态阈值）、季节性异常检测 | 无 ML 异常检测，漏检慢速内存泄漏等 |
| **多信号融合** | 指标 + 日志（SSH 采集） | Metrics + Logs + Traces 统一摄取 | 无分布式 Trace 支持 |
| **预测性运维** | 无 | 容量预测、磁盘预警（提前 N 天） | 无预测能力，只能被动响应 |
| **自动回滚** | 无（edit_remote_config 有备份） | 变更回滚一键触发，与 CMDB 集成 | 有备份但无自动回滚触发器 |
| **事件集成** | 无 | Jira/ServiceNow 自动开单，Slack/PagerDuty 通知 | 缺少外部通知和工单集成 |
| **RBAC** | 单一 token（有/无） | 细粒度角色权限（查看/操作/审批分离） | 无角色区分 |
| **多集群支持** | 单集群（SERVICE_MAP 硬编码） | 跨集群、跨云统一视图 | 无多集群架构 |
| **SLO/SLA 追踪** | 无 | 服务可用性 SLO 实时计算、告警 | 无 |
| **历史趋势分析** | audit_log 只存操作记录 | 时序 DB + 可视化趋势图 | 无时序存储和趋势展示 |
| **对话式运维** | 无（只有 auto/fix 两种固定模式） | Chat with your data（自然语言查询集群状态） | 缺少 ad-hoc 对话接口 |
| **Runbook 自动化质量** | 置信度≥0.7 才写，人工审批 | AI 自动生成 + 评分 + 版本管理 | 质量门控简单，无版本 diff |

---

## 9. 可借鉴的特性清单

以下是结合项目现有架构、hackathon 时间窗口和技术可行性筛选出的高价值特性。

### 🔴 高优先级（直接影响评分和 Demo 质量）

#### F1：对话式运维入口（Chat Mode）
在 Web 控制台增加一个聊天框，允许用户用自然语言提问（"NameNode 今天发生了什么？"、"帮我查一下 DataNode 内存"），后端创建一个 `chat` 模式的 `ReActAgent`，工具权限同 `auto` 模式（只读）。  
**实现成本：** 低（复用现有 ReActAgent + EventBus + WebSocket）  
**亮点：** 这是商业 AIOps 产品的标志性功能，Demo 展示效果极好。

#### F2：告警关联与去重
在 `get_pending_alerts` 中对相同服务的多个告警做聚合（同服务告警合并为一个 fix 任务），避免同一服务触发多次重复 fix。  
**实现成本：** 低（几十行逻辑）

#### F3：一键回滚
`edit_remote_config` 已经做了 `.bak.<ts>` 备份。增加一个 `rollback_config(service, node, file)` 工具，找到最新备份并恢复，同时记录 audit log。  
**实现成本：** 低

#### F4：健康总览 Dashboard
参见 6.1，增加首页仪表板。**实现成本：** 中（需要新 React 组件 + 后端 snapshot API）

### 🟡 中优先级（提升项目完整度）

#### F5：外部通知 Webhook
当 fix 完成或告警升级时，向配置的 Webhook URL 推送通知（支持 Slack/钉钉/企业微信格式）。  
**实现成本：** 低（`requests.post` to webhook URL）

#### F6：事后报告自动生成（Post-mortem）
fix 会话结束后，自动生成一份 Markdown 格式的事后分析报告（时间线 + 根因 + 修复动作 + 影响评估），存入知识库并可从 UI 下载。  
**实现成本：** 中（LLM 生成 + 模板）

#### F7：时序指标存储与趋势图
用 SQLite 的时序表（`(ts, node, metric, value)`）记录每次 `get_metrics` 的数值，前端用 Ant Design Charts 展示 24h 趋势折线图。  
**实现成本：** 中

#### F8：GPU 监控集成
增加 `get_gpu_metrics` 工具，SSH 到 GPU 服务器运行 `rocm-smi --json`，返回 GPU 利用率、显存使用、温度。在 Demo 中展示 AMD Radeon 的实时状态。  
**实现成本：** 低，且直接切合比赛 AMD 主题

### 🟢 低优先级（锦上添花）

#### F9：Runbook 版本 Diff
runbook 更新时保留历史版本，UI 支持查看 diff。  

#### F10：多语言系统 Prompt（中/英自动切换）
根据用户浏览器语言或显式设置，切换 Agent 的系统 prompt 语言，降低工具调用 JSON 格式错误率（英文 prompt 对结构化输出更稳定）。

#### F11：Prometheus 指标导出
从 FastAPI 暴露 `/metrics` 端点（用 `prometheus-fastapi-instrumentator`），实现标准化可观测性接入。

---

## 10. 优先级汇总

| 编号 | 问题/优化点 | 类别 | 优先级 | 估计工作量 |
|------|------------|------|--------|----------|
| 1.1 | `app.py` ImportError 启动崩溃 | Bug | P0 🔴 | 1 行 |
| 5.1 | M7 Demo + 视频录制 + Docker | 合规 | P0 🔴 | 3-5 天 |
| 1.2 | Runbook 向量清空后不重建 | Bug | P1 🔴 | 1 行 |
| 4.1 | bootstrap.sh 硬编码密钥 | 安全 | P1 🔴 | 0.5h |
| F1 | 对话式运维 Chat Mode | 功能 | P1 🔴 | 0.5 天 |
| F2 | 告警聚合去重 | 功能 | P1 🔴 | 2h |
| 2.1 | Orchestrator 主线程阻塞 | 架构 | P2 🟡 | 1h |
| 2.2 | 审批阻塞 Agent 线程 | 架构 | P2 🟡 | 2h |
| 2.3 | EventBus 内存泄漏 | 架构 | P2 🟡 | 1h |
| 3.1 | 向量相似度纯 Python 性能差 | 代码 | P2 🟡 | 0.5h |
| 6.1 | 缺少集群健康总览 | UX | P2 🟡 | 半天 |
| 6.2 | 无法中止运行中会话 | UX | P2 🟡 | 2h |
| 6.3 | 审批无推送通知 | UX | P2 🟡 | 1h |
| 7.1 | Docker-compose Hadoop 环境 | 部署 | P2 🟡 | 1-2 天 |
| F8 | GPU 监控集成（AMD 主题） | 功能 | P2 🟡 | 2h |
| 1.3 | Circuit Breaker 竞态 | Bug | P3 🟢 | 1h |
| 2.7 | CM API 无重试 | 架构 | P3 🟢 | 0.5h |
| 3.2 | BM25 归一化符号错误 | 代码 | P3 🟢 | 0.5h |
| 3.5 | inject_fault 空函数注册工具 | 代码 | P3 🟢 | 15min |
| 3.7 | print 混用，无统一 logging | 代码 | P3 🟢 | 2h |
| 4.4 | API 无速率限制 | 安全 | P3 🟢 | 1h |
| 5.2 | AMD GPU 展示不突出 | 合规 | P3 🟢 | 1h |
| 5.5 | 缺少可复现 Demo 脚本 | 合规 | P3 🟢 | 2h |
| 7.3 | 缺少控制平面 Dockerfile | 部署 | P3 🟢 | 2h |
| 7.5 | 缺少 /health 端点 | 部署 | P3 🟢 | 0.5h |
| F3 | 一键回滚 | 功能 | P3 🟢 | 2h |
| F5 | Webhook 通知 | 功能 | P4 ⚪ | 1h |
| F6 | 事后报告自动生成 | 功能 | P4 ⚪ | 半天 |
| F7 | 时序指标趋势图 | 功能 | P4 ⚪ | 1 天 |

---

## 附录：云端服务器实探结果（2026-07-18）

> 通过 `ssh root@36.150.116.206 -p 31036` 实际访问了 AMD Radeon 云端推理服务器，以下为直接观测结果，补充和修正了部分静态代码分析的推断。

### A.1 服务器硬件规格（已确认）

| 项目 | 规格 |
|------|------|
| GPU | AMD Radeon Graphics，Device ID `0x744b`，**48GB VRAM**（~22GB 已占用） |
| GPU 功率上限 | 241W |
| CPU | AMD EPYC 9334 32-Core Processor（128 线程） |
| 内存 | 503GB，当前仅用 23GB |
| 系统 | Ubuntu 24.04.4 LTS（容器化，overlay 文件系统） |
| Workspace | 98GB loop device，16GB 已用（模型文件），78GB 空闲 |

Device `0x744b` + 48GB VRAM 与 AMD Radeon W7900D 规格完全吻合（W7900D = 48GB HBM3）。README 中的宣称属实。

### A.2 推理服务当前状态（已确认可用）

- **llama-server**（PID 9729）正在运行，监听 `0.0.0.0:8080` ✅
- 直接 curl `http://127.0.0.1:8080/v1/models` 响应正常，模型已加载
- 模型参数：27.3B params，context 131072，MTP `--spec-draft-n-max 1` 已激活
- VRAM 占用约 22GB（与 Q4_K_M 量化下 27B 模型预期的 ~16-18GB + KV cache 吻合）

**重要异常**：`/workspace/llama-server.log` 显示的是一次**失败的重启尝试**（"couldn't bind port 8080"），这是因为 bootstrap.sh 第二次运行时，端口已被 PID 9729 占用，新进程启动失败并用 `>` 覆盖了日志，把原成功启动的日志抹掉了。当前真实服务是正常的。

### A.3 新增问题：bootstrap.sh 的日志覆盖 Bug

**文件：** `/workspace/bootstrap.sh`  
**问题：** `nohup ./llama-server ... > /workspace/llama-server.log 2>&1 &` 使用 `>` 截断覆盖，每次 bootstrap.sh 重新运行都会清空历史日志，且已有进程的成功启动记录会被新进程的失败信息覆盖（如上所述）。  
**修复：**
```bash
# 改为追加 + 时间戳分隔
nohup ./llama-server ... >> /workspace/llama-server.log 2>&1 &
echo "===== llama-server start $(date) =====" >> /workspace/llama-server.log
```

### A.4 新增问题：llama-server 暴露在 `0.0.0.0`，API key 在进程列表明文可见

**问题：**  
1. `--host 0.0.0.0` 让 llama-server 监听所有网卡，若云服务商端口映射了 8080，则任何人用 `fengfeng123` 即可调用推理 API（高消耗操作）。  
2. `ps aux` 输出中 `--api-key fengfeng123` 明文可见（任何有 shell 访问权限的用户均可读取）。  

**建议：**  
- 改为 `--host 127.0.0.1`，仅本地可访问（SSH 隧道已经解决远程访问问题）；  
- 通过环境变量传递 API key（`--api-key $LLAMA_API_KEY`），从 `/proc/<pid>/cmdline` 读不到原始值。

### A.5 新增问题：bootstrap.sh 缺少 `set -e`，错误静默

**问题：** 脚本开头是 `set -uo pipefail`，缺少 `-e`（遇错退出）。这意味着 modelscope 下载失败、llama.cpp 二进制缺失等关键错误不会终止脚本，后续步骤继续执行，产生误导性的"OK"输出。  
**修复：** 改为 `set -euo pipefail`，或在关键步骤显式检查退出码。

### A.6 新增问题：pip3 `--break-system-packages` 污染系统 Python

**问题：** Ubuntu 24.04 引入了 PEP 668，禁止直接 `pip install` 到系统 Python，bootstrap.sh 用 `--break-system-packages` 绕过。这在容器环境不会造成系统层破坏，但破坏了 Python 包管理的隔离性，不同工具版本可能冲突。  
**建议：** 改为在 `/workspace/venv` 创建虚拟环境后安装：
```bash
python3 -m venv /workspace/venv && source /workspace/venv/bin/activate
pip install modelscope sentence-transformers ...
```

### A.7 机会：`amdsmi` 库已安装，可直接实现 GPU 监控工具

服务器已安装 `amdsmi 26.2.2`（AMD 官方 Python 库，比 `rocm-smi` CLI 调用更高效）。可直接用于实现 F8 建议的 `get_gpu_metrics` 工具：

```python
import amdsmi

def get_gpu_metrics() -> dict:
    amdsmi.amdsmi_init()
    handles = amdsmi.amdsmi_get_processor_handles()
    h = handles[0]
    return {
        "gpu_util": amdsmi.amdsmi_get_gpu_activity(h)["gfx_activity"],
        "vram_used_mb": amdsmi.amdsmi_get_gpu_memory_usage(h, amdsmi.AmdSmiMemoryType.VRAM) // (1024*1024),
        "vram_total_mb": amdsmi.amdsmi_get_gpu_memory_total(h, amdsmi.AmdSmiMemoryType.VRAM) // (1024*1024),
        "temp_c": amdsmi.amdsmi_get_temp_metric(h, amdsmi.AmdSmiTemperatureType.EDGE, amdsmi.AmdSmiTemperatureMetric.CURRENT),
        "power_w": amdsmi.amdsmi_get_power_info(h)["average_socket_power"],
    }
```
这个工具可以在 Demo 中展示 AMD Radeon GPU 的实时状态，直接切合比赛 AMD 主题，实现成本仅约 1-2 小时。

### A.8 项目代码未在 GPU 服务器上部署（设计确认）

GPU 服务器仅运行推理层（llama-server），Python 环境里只有 `requests`、`modelscope` 等基础包，没有 FastAPI、sentence-transformers、项目代码。控制平面（AIOps Agent）运行在本地机器，通过 SSH 隧道 `localhost:18080 → 36.150.116.206:8080` 访问推理服务。这是合理的架构分离，但意味着：

- SSH 隧道是单点故障（已在 7.6 节提及），服务器重启后需手动重建隧道
- GPU 服务器断线期间，控制平面的所有 LLM 调用均会失败，Orchestrator 无法工作
- **建议：** 在 Orchestrator 启动时探活 LLM 端点，若连接失败则暂停调度并记录告警，而非让错误传播至每个 Agent 迭代

---

---

## 附录 B：集群节点实探结果（2026-07-18）

> 通过 SSH 直连三个 Hadoop 节点（192.168.6.176/177/178）并调用 CM API 获得，反映集群当前真实状态。

### B.1 节点概览

| 节点 | IP | 主要角色 | 内存(可用) | OS磁盘(用量) | 在线天数 |
|------|----|---------|-----------|------------|---------|
| hadoop01 | 192.168.6.176 | SecondaryNN / DataNode / NodeManager / ZK / Oozie / Hive | 9.4 GB | 64G/300G (22%) | 48天 |
| hadoop02 | 192.168.6.177 | **NameNode** / DataNode / NodeManager / **ResourceManager** / ZK | 10.3 GB | 151G/300G (51%) | 48天 |
| hadoop03 | 192.168.6.178 | DataNode / NodeManager / ZK / **CM Server** / ServiceMonitor | 5.1 GB | 159G/300G (53%) | 48天 |

**CM Health**：所有服务（ZooKeeper / HDFS / YARN / Hive / Spark / Oozie / Hue）全部 GOOD，无当前告警。

**HDFS 整体状态**：1.11 TB 总容量，已用 5.02 GB（0.44%），无 under-replicated / corrupt / missing 块，3 个 DataNode 全部 live。

### B.2 发现的集群层问题

#### B.2.1 ⛔ Hive MetaStore JVM 堆只有 50 MB

**确认命令**：`ps aux | grep HiveMetaStore | grep -oE '\-Xm[xs][0-9]+'` → `-Xms52428800 -Xmx52428800`

52428800 字节 = **50 MB**，这是 CDH 向导误配或手工改小的结果。CDH 官方建议最低 256 MB，生产建议 1-2 GB。后果：
- 任何中等 Hive 查询都会触发频繁 Full GC → 查询超时
- 高并发时直接 OOM，`EmbeddedOozieServer` 和 HiveServer2 也会级联失败
- 项目的 seed runbook "DataNode OOM"其实更该是 "HiveMetaStore OOM"

**修复**：CM GUI → Hive → HiveMetaStore → Java Heap Size，改为至少 512 MB；或直接在 CM 参数页修改 `hive_metastore_java_heapsize`。

#### B.2.2 ⚠ hadoop03 过度拥挤，内存持续紧张

该节点同时运行：CM Server（~2.3 GB heap）+ ServiceMonitor（~1.8 GB heap）+ AlertPublisher + EventCatcher + DataNode + NodeManager + ZooKeeper + **ClickHouse**（732 MB）+ **MySQL 8.0.33**（537 MB）。

实测可用内存仅 **5.1 GB**，free 曾低至 **230 MB**（真实空闲，非 available），Swap 已占用 71 MB。若 CM Server 发生 Full GC 或 ClickHouse 突发查询，可能触发 OOM Killer，导致 CM 服务或 MySQL 进程被杀，进而引起 Hive MetaStore 连接失败。

**建议**：将 CM ServiceMonitor 或 ClickHouse 迁移到 hadoop01 / 02；或在 CM 中降低 ServiceMonitor 最大堆至 512 MB（当前 1 GB）。

#### B.2.3 ⚠ ClickHouse 在三节点均运行，但完全不在监控范围内

三个节点均有 `/usr/bin/clickhouse-server` 进程，CPU 利用率高峰时达 9–17%（`ps aux` 实测），消耗显著。

- **项目 SERVICE_MAP 中没有 ClickHouse 条目**
- `INSPECT_SERVICES` 不包含 ClickHouse
- `get_alerts()` 只通过 CM API 采集健康检查，ClickHouse 不受 CM 管理，告警完全盲区
- 若 ClickHouse 占满 CPU 导致 YARN 任务超时，AIOps Agent 看到的现象是 NodeManager 异常，根因诊断链会走弯路

**建议**：在 SERVICE_MAP 中增加 ClickHouse 条目（SSH 采集 `systemctl status clickhouse-server` + `clickhouse-client --query "SELECT * FROM system.metrics"`），加入巡检列表。

#### B.2.4 ⚠ hadoop02/03 OS 根分区已用 51%/53%，且有持续增长风险

- HDFS 数据盘（/data0, /data1 各 200 GB）目前几乎空置（各用 1.2 GB，< 1%），这是好事
- 但 OS 根分区包含所有服务日志（`/var/log/hadoop-*`、`/var/log/hive` 等）
- CDH 6.3.2 默认日志 rolling 策略较保守，长期运行会把根分区撑满
- 一旦根分区满，CM Agent、SSH、所有服务日志写入都会失败

**建议**：检查 `/var/log` 占用并设置 logrotate；或将日志目录软链到 /data0。

#### B.2.5 MySQL 8.0.33 单点在 hadoop03

Hive MetaStore 和 CM Server 的元数据库都在 hadoop03 上的这个 MySQL 实例。若 hadoop03 内存耗尽导致 MySQL 被 OOM Killer 杀掉，后果是：Hive 完全不可用 + CM Server 失联（整个管控面瘫痪）。

**建议**：为 MySQL 设置内存上限（`innodb_buffer_pool_size`），或监控 hadoop03 的可用内存，在低于阈值时提前告警。

#### B.2.6 OpenSSH 未启用抗量子密钥交换（低优先级）

每次 SSH 连接都有警告 `connection is not using a post-quantum key exchange algorithm`。这是 OpenSSH 9.x 的新警告，对当前安全无实际影响，但影响自动化脚本的日志清洁度。

项目 `tools.py` 的 SSH 命令可加 `-o KexAlgorithms=sntrup761x25519-sha512@openssh.com` 以消除警告（需先确认服务端 OpenSSH 版本支持）。

### B.3 集群对项目的影响

| 影响点 | 说明 |
|-------|------|
| Hive MetaStore OOM 是高概率告警 | 50MB 堆在真实查询下必然触发，是最佳 Demo 演示故障点 |
| ClickHouse 盲区会干扰根因分析 | Agent 会把 ClickHouse 抢占 CPU 导致的问题归咎于 YARN |
| hadoop03 内存压力影响 CM API 响应 | CM API 偶发超时会触发 `tools.py` 的异常路径，需保证重试逻辑 |
| HDFS 数据盘充裕 | Demo 演示 HDFS 磁盘告警需手动制造数据 |
| 集群整体健康、无当前告警 | Demo 前需手动触发故障（如停 HiveMetaStore）才能展示 fix 流程 |

---

*本报告基于代码静态分析 + 云端服务器实际探查 + 集群节点实探（2026-07-18 UTC）。部分观察依赖对代码逻辑的推断，实施修复前建议先通过测试验证复现。*

| 3.2 | BM25 归一化符号错误 | 代码 | P3 🟢 | 0.5h |
| 3.5 | inject_fault 空函数注册工具 | 代码 | P3 🟢 | 15min |
| 3.7 | print 混用，无统一 logging | 代码 | P3 🟢 | 2h |
| 4.4 | API 无速率限制 | 安全 | P3 🟢 | 1h |
| 5.2 | AMD GPU 展示不突出 | 合规 | P3 🟢 | 1h |
| 5.5 | 缺少可复现 Demo 脚本 | 合规 | P3 🟢 | 2h |
| 7.3 | 缺少控制平面 Dockerfile | 部署 | P3 🟢 | 2h |
| 7.5 | 缺少 /health 端点 | 部署 | P3 🟢 | 0.5h |
| F3 | 一键回滚 | 功能 | P3 🟢 | 2h |
| F5 | Webhook 通知 | 功能 | P4 ⚪ | 1h |
| F6 | 事后报告自动生成 | 功能 | P4 ⚪ | 半天 |
| F7 | 时序指标趋势图 | 功能 | P4 ⚪ | 1 天 |

---

*本报告基于代码静态分析，未进行实际集群环境运行测试。部分观察依赖对代码逻辑的推断，实施修复前建议先通过测试验证复现。*


