# 项目总体 Todo — Radeon AIOps Agent

> 单一待办源。设计：`docs/DESIGN.md`（§21 安全护栏 / §22 商业差距与可借鉴特性）。
> 勾选框：`- [x]` 已完成 / `- [ ]` 待做。按里程碑与领域组织，呈现整体进度。

---

## 进度总览

| 区块 | 状态 | 说明 |
|---|---|---|
| M1 推理基座 | ✅ 完成 | llama.cpp ROCm / 模型 / tool-calling / 性能基线 |
| M2 工具层 + ReAct | ✅ 完成 | SSH+CM API 真实工具；docker 环境待切 |
| M3 编排层 | ✅ 完成 | Orchestrator / master / 子session / 抢占 |
| M4 安全护栏（初版） | ✅ 完成 | 风险分级 / dry-run / 审批 / 审计 / 熔断 |
| 缺陷修复轮次 | ✅ 完成 | 7 项核心缺陷 + 20 项静态审查 |
| §21 安全护栏**重设计** | ✅ 完成 | 双轴+四档+DB规则+attempt节流（T1–T8 已落地，含评审修复） |
| M5 知识库 + 学习闭环 | ✅ 完成 | sqlite-vec(降级FTS5) / write_runbook / 审核流程 |
| M6 Web 控制台 | ✅ 完成 | 后端+前端；Grafana 嵌入 / admin 美化待补 |
| M7 演示与提交 | ⬜ 待做 | 多剧本 / 录屏 / README / 性能数据 |

---

## 一、已交付（Done）

### M1 — 推理基座
- [x] llama.cpp ROCm/HIPBLAS 编译验证（ldd 确认 ROCm 库）
- [x] Qwen27B Q4_K_M + KV q8_0 + FA + mmap + prompt-cache 跑通
- [x] tool-calling 格式验证
- [x] thinking 模型 `reasoning_content` 独立字段确认
- [x] 性能基线测量（生成 ~29 t/s、prompt 衰减曲线、VRAM 21.7G/51.5G）
- [x] MTP 投机解码启用 + 参数调优（`--spec-type draft-mtp`，基准测试 n_max=1~8，最优 n_max=1 达 37.5 t/s +30%）

### M2 — 工具层 + 单 session ReAct
- [x] `get_service_status` / `get_alerts` / `get_metrics` / `read_logs`（预压缩）/ `search_kb` / `restart_service`（CM API）/ `hdfs_admin`
- [x] 手写 ReAct 循环跑通故障剧本
- [x] docker-compose 3 节点 Hadoop + Prometheus + Alertmanager + Grafana — **Phase 1 Step 1 已交付**: HDFS HA(2NN+3DN+3JN+ZKFC) + YARN HA(2RM+3NM+JHS) + ZK quorum, MR Pi 验证通过.
- [ ] Hive on Tez + HBase + MySQL (Phase 1 Step 2, 待做)

### M3 — 编排层
- [x] Orchestrator 常驻 + master 纯规则调度
- [x] session manager（用完即焚子 session）
- [x] `/auto` 巡检 loop + `/fix` 告警抢占
- [x] SQLite 落库（sessions / events / cluster_state）
- [x] 上下文策略：一次性 context + DB 状态卡 + 工具输出预压缩
- [x] 端到端闭环验证：停 DataNode / 停 NameNode → 检测 → 诊断 → 重启 → 验证

### M4 — 安全护栏（初版）
- [x] 风险分级 low/medium/high/destructive
- [x] dry-run 预演
- [x] 审批门（console 自动 / web 等人工）
- [x] 审计日志（audit_log）
- [x] 熔断（连续失败上限 + 冷却，类级跨会话共享）
- [x] 回滚机制（`edit_remote_config` 先备份 `.bak.<ts>`，替换失败自动回滚，见 §21 T5）

### 缺陷修复轮次（评审发现并已修）
- [x] 状态卡数据结构（后端 `services` 改回对象 + `overall_health`）
- [x] 流式输出结束被截断 300 字（去掉 `[:300]`）
- [x] 审批流形同虚设（`AUTO_APPROVE` 接入 Guardrail）
- [x] SQLite 跨线程（RLock + `store.lock`）
- [x] CM 轮询风暴（10s 缓存）+ 快速失败（Retry(0) + timeout 3/5）
- [x] 熔断永不触发（`_is_failed` 加 `error`/`circuit_broken`）
- [x] 单节点重启误会（tool 描述/hint 写明 CM 以服务为单位）
- [x] 静态 20 项：命令注入转义 / CORS / 审批流程 / 流式异常 / 死代码 / 前端错误处理等

### §21 安全护栏分级自治（设计 + 实施均完成，T1–T8）
- [x] DESIGN §21 落地：双轴（AUTONOMY × tier）+ 四档 + DB 规则 + attempt 节流
- [x] 关键决议确认：定级不归模型 / recover 仅 DOWN / irreversible 永不自动
- [x] **T1.** `risk_rules` 表 + 迁移 + 种子（`*` 默认 fail-closed → irreversible）
- [x] **T2.** `classify()`：查 `risk_rules`（TTL 缓存）+ `match_json` 命中 + fail-closed；替代静态 `TOOL_RISK`
- [x] **T3.** `Guardrail` 四档分支 + `AUTONOMY` 轴：supervised 走审批 / autonomous 按档策略 / 未授权立即升级（不卡 600s）
- [x] **T4.** 高危尝试节流：按 `(tool,target)` 查 `audit_log` 派生计数（覆盖 recover+reversible）+ 冷却 + 超限升级
- [x] **T5.** `reversible` 落地：`edit_remote_config` 先 `cp .bak.<ts>` 再改再 reload，sed 注入已用 `python3 -c`+`shlex.quote` 修复
- [x] **T6.** `config`：`AUTONOMY=supervised|autonomous` 替换 `AUTO_APPROVE`（默认 supervised 安全）
- [x] **T7.** Web API + 管理页面：`risk_rules` CRUD + 管理面"风险规则"页（irreversible 档禁勾 autonomous，后端双重强制）
- [x] **T8.** 联调修复：熔断改类级跨会话共享；`_refine_restart` 加 10s 缓存；无人值守 DOWN 自动重启重试 / irreversible 拒绝并升级告警

### M5 — 知识库 + 学习闭环 ✅
- [x] `runbooks` 表 + FTS5 全文索引 + 触发器同步 (db.py)
- [x] 种子数据 6 条 runbook (DataNode OOM / NameNode GC / NodeManager 掉线 / 磁盘满 / ZK 超时 / NameNode SIGTERM)
- [x] `kb.py`: bge-small-zh 嵌入模型 (CPU, 懒加载) + numpy 余弦相似度向量检索
- [x] 混合检索: 向量 top-k + BM25 top-k 合并去重, 向量优先; 缺 bge 自动降级 BM25
- [x] `search_kb` 工具重写: 接入混合检索, content 截断防 context 膨胀
- [x] `write_runbook` 工具: 置信度门控 (<0.7 拒绝) + pending_review 状态 + session_id 关联
- [x] `guardrails.py`: write_runbook session_id 注入 + 低危自动执行
- [x] Web API: runbooks CRUD + `/review` 审核接口 + `/search` 检索测试 + `/stats` 统计
- [x] Web 前端: 知识库管理页面 (统计卡片 / 检索测试 / 列表 / 审核 / CRUD)
- [x] `agent.py` 学习闭环: fix 模式修复成功后自动提示回写 runbook (检测修复关键词 + 未调用 write_runbook)
- [x] FIX_PROMPT 更新: 引导 agent 修复成功后调用 write_runbook
- [x] 测试: `tests/test_m5_kb.py` 验证 DB/FTS/CRUD/write_runbook/审核/置信度门控

---

## 二、待做 — 其他领域

- [ ] **T9. 演示与提交（M7）** — 多故障剧本跑通 / 端到端录屏 / README 复现步骤+架构图 / 性能数据
- [ ] **M6 补** — 集群状态嵌 Grafana / 前端 admin 模板美化
- [ ] **环境** — 切 docker-compose（Apache Hadoop + Prometheus + Alertmanager + Grafana），可复现供评委

### 提交 PR 前 check（临交付时统一处理）
- [ ] **DESIGN.md 英文化** — 与 README 一致，英文作默认主文件、中文保留：`DESIGN.md`(英文) + `DESIGN_ZH.md`(中文)，顶部加语言互链。当前中文版内容已定稿，仅差翻译，勿提前译（避免后续改动重复翻译）
- [ ] 文档交叉引用最终核对（README/DESIGN/TODO 互链、文件结构清单与实际一致）

---

## 三、审计待办（2026-07-18 项目审查，按优先级）

> 已修复 7 项（app.py 导入崩溃 / runbook 向量清空 / kb numpy+BM25 / 熔断+缓存加锁 / 密钥脱敏），不再列。
> 下列为审查发现且**尚未处理**的项，实施后勾选。

### P1 — 冲刺高价值（直接影响评分/Demo）
- [ ] **对话式运维 Chat Mode** — Web 加聊天框，自然语言提问集群状态，后端复用 `ReActAgent`（工具权限同 auto 只读）。复用现有 EventBus+WebSocket，成本低、Demo 效果强
- [ ] **告警聚合去重** — `get_pending_alerts` 对同服务多告警合并为一个 fix 任务，避免重复 fix（几十行）
- [ ] **GPU 监控工具 `get_gpu_metrics`** — 用服务器已装的 `amdsmi` 库返回利用率/显存/温度，Demo 展示 AMD 主题（1-2h）
- [ ] **健康总览 Dashboard** — Web 首页展示服务状态卡/活跃告警/最近 fix/autonomy 徽章

### P2 — 架构健壮性
- [x] **Orchestrator 异常兜底** — 循环体 `try/except`+5s 退避重试，单轮 LLM 断连/CM 超时不再终止常驻进程；orch 主线程 + web daemon 线程，目标已达成未再拆独立线程
- [ ] **审批不阻塞调度** — `supervised` 审批等待（现轮询 SQLite 最长 600s）改异步唤醒，或缩短超时并降级 reject，避免冻结 Orchestrator
- [ ] **EventBus 孤儿队列清理** — WebSocket 异常断开时保证 `unsubscribe`，或队列加 TTL，防内存泄漏
- [ ] **LLM 端点探活** — `LLMClient` 加 ping `/v1/models`，SSH 隧道断连时暂停 Orchestrator 并告警，而非静默失败传播到每次 Agent 迭代
- [ ] **CM API 重试** — `Retry(total=0)` 改 `Retry(total=2, backoff_factor=0.5, status_forcelist=[502,503,504])`
- [ ] **会话可中止** — Web 加"终止会话"按钮，`sessions.status=cancelled`，Agent 每轮迭代开始检查并提前退出

### P2 — 交互易用
- [ ] **审批实时推送** — 新审批请求经 WebSocket 推浏览器通知 / 顶部 `notification.warning`，Sider 审批项加数字 Badge
- [ ] **Agent 进度提示** — 会话卡片显示"迭代 N/15"+已耗时
- [ ] **工具结果友好渲染** — `read_logs` 错误行红/警告行黄高亮；`get_metrics` 用数值卡片替代原始 JSON
- [ ] **知识库搜索强化** — 搜索框提升为首要元素，结果显示摘要+分数+关键词高亮

### P3 — 部署与合规
- [ ] **一键 Demo 脚本** — `scripts/demo.sh`：启动→注入故障→触发 fix→展示控制台，供评委复现
- [ ] **控制平面 Dockerfile + compose** — Python 后端 / React 前端(nginx) / 整体编排；前端 `web/dist` 挂到 FastAPI StaticFiles
- [ ] **`/health` 端点** — 返回状态 + LLM 可达性，供 Docker healthcheck
- [ ] **`requirements` 固定版本** — `pip freeze` 或 `pyproject.toml + uv lock`，保证复现
- [ ] **前端 API 地址可配** — Vite `import.meta.env.VITE_API_URL`，勿硬编码
- [ ] **README 突出 AMD** — GPU 监控截图 + 推理性能对比图 + ROCm/HIPBLAS/FA/KV量化说明 + Agentic 特性展示节

### P3 — 安全加固
- [ ] **Web 默认鉴权** — `CONSOLE_TOKEN` 空时默认生成随机 token 打印到启动日志，而非放行所有
- [ ] **工具入参 Pydantic 校验** — 类型/长度/正则，guardrail 层二次校验，拦截超长/特殊字符（尤其 `edit_remote_config`、`hdfs_admin`）
- [ ] **`edit_remote_config` 配置语法校验** — 替换后 `xmllint --noout` 校验 XML，写入前 diff 预览记审计
- [ ] **API 速率限制** — `slowapi` 对写操作限流（如 10 req/min）
- [ ] **CORS 生产收紧** — origins 提取到配置，生产环境显式设置

### P3 — 代码质量
- [ ] **统一 logging** — 替换散落的 `print()`，标准 `logging` 分级，为 Web 提供日志流
- [ ] **ReAct 上下文预算** — 追踪 `usage`，剩余窗口低于阈值时提前收尾/滑窗压缩，防 15 轮×2048 累积截断
- [ ] **工具 schema 与签名同步** — `@tool` 从类型注解+docstring 生成 JSON Schema，或启动时校验一致性

### P4 — 锦上添花特性
- [ ] **一键回滚工具** — `rollback_config` 找 `.bak.<ts>` 最新备份恢复并记审计（备份逻辑已有）
- [ ] **外部通知 Webhook** — fix 完成/告警升级推送 Slack/钉钉/企业微信
- [ ] **事后报告自动生成** — fix 结束生成 Markdown post-mortem（时间线+根因+动作+影响），存 KB 可下载
- [ ] **时序指标趋势图** — SQLite 时序表记录 `get_metrics`，前端 24h 趋势折线
- [ ] **Runbook 版本 Diff** — 更新保留历史版本，UI 查看 diff
- [ ] **并行 Fix 会话** — 同类型服务 fix 并行，同服务操作服务粒度锁串行
- [ ] **Prometheus 指标导出** — FastAPI `/metrics`（`prometheus-fastapi-instrumentator`）

---

## 四、与商业 AIOps 的差距（选型参考）

> 详见 `DESIGN.md` §22。核心短板：告警关联去重、RCA 置信度、预测性运维、对话式运维、时序趋势、外部工单/通知集成。上表 P1/P4 已挑出性价比最高的几项落地。

---

## 待敲定参数（设计决策，非实现阻塞）
- `MAX_ATTEMPTS` / `cooldown` / 观察窗口 `WINDOW`（建议 2 次 / 30–60s / 10min）
- 管理页面是否首版必须（可后补）
- 通知 webhook（飞书/钉钉，可选）
- 审批超时（现 10min）/ 巡检周期（现 5min）/ `-t` 线程（现 16，EPYC 128 线程可试 32）

## 其他遗留（仅记录）
- 集群环境：临时 CDH 6.3.2（176/177/178）→ docker-compose
- CM API 单角色操作不支持（v30 仅整服务 commands/restart），recover 按服务单位执行
- MTP 已生效（`--spec-type draft-mtp --spec-draft-n-max 1`），最优参数经基准测试确认，+30% 加速
