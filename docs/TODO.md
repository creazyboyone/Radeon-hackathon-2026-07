# 项目总体 Todo — Radeon AIOps Agent

> 单一待办源。设计：`docs/DESIGN.md`（§21 为安全护栏最终方案）。
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
| §21 安全护栏**重设计** | 🔧 设计中完成，**实施待做** | 双轴+四档+DB规则+attempt节流 |
| M5 知识库 + 学习闭环 | ⬜ 待做 | sqlite-vec / write_runbook |
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

### M2 — 工具层 + 单 session ReAct
- [x] `get_service_status` / `get_alerts` / `get_metrics` / `read_logs`（预压缩）/ `search_kb` / `restart_service`（CM API）/ `hdfs_admin`
- [x] 手写 ReAct 循环跑通故障剧本
- [ ] docker-compose 3 节点 Hadoop + Prometheus + Alertmanager + Grafana（当前临时 CDH 6.3.2）

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
- [x] 熔断（连续失败上限 + 冷却）
- [ ] 回滚机制（为 `edit_remote_config` 预留，见 §21 T5）

### 缺陷修复轮次（评审发现并已修）
- [x] 状态卡数据结构（后端 `services` 改回对象 + `overall_health`）
- [x] 流式输出结束被截断 300 字（去掉 `[:300]`）
- [x] 审批流形同虚设（`AUTO_APPROVE` 接入 Guardrail）
- [x] SQLite 跨线程（RLock + `store.lock`）
- [x] CM 轮询风暴（10s 缓存）+ 快速失败（Retry(0) + timeout 3/5）
- [x] 熔断永不触发（`_is_failed` 加 `error`/`circuit_broken`）
- [x] 单节点重启误会（tool 描述/hint 写明 CM 以服务为单位）
- [x] 静态 20 项：命令注入转义 / CORS / 审批流程 / 流式异常 / 死代码 / 前端错误处理等

### §21 设计（已完成，待实施）
- [x] DESIGN §21 落地：双轴（AUTONOMY × tier）+ 四档 + DB 规则 + attempt 节流
- [x] 关键决议确认：定级不归模型 / recover 仅 DOWN / irreversible 永不自动

---

## 二、进行中 / 待实施 — §21 安全护栏分级自治

> 设计见 `docs/DESIGN.md` §21。以下为落地清单。

- [ ] **T1. `risk_rules` 表 + 迁移 + 种子** — schema 见 §21.3；首启若空从 `tools.py:TOOL_RISK` 灌默认
- [ ] **T2. `classify()` 实现** — 查 `risk_rules`（TTL 缓存）+ `match_json` 命中 + fail-closed 兜底；替代静态 `TOOL_RISK`
- [ ] **T3. `Guardrail` 四档分支 + `AUTONOMY` 轴** — `recover`/`reversible`/`irreversible`/`low|medium`；supervised 等人工，autonomous 按档策略
- [ ] **T4. 高危尝试节流** — 按 `(tool,target)` 查 `audit_log` 派生计数（覆盖 recover+reversible）+ 冷却 + 超限升级
- [ ] **T5. `reversible` 落地** — `edit_remote_config`：先 `cp file file.bak.<ts>` 再改再 reload，留回滚点
- [ ] **T6. `config` `AUTONOMY` 模式** — 用 `AUTONOMY=supervised|autonomous` 替换 `AUTO_APPROVE`
- [ ] **T7. Web API + 管理页面** — `risk_rules` CRUD + 管理面"风险规则"页（`irreversible` 档禁勾 `autonomous`）
- [ ] **T8. 联调** — 无人值守端到端：DOWN 自动重启重试 / irreversible 拒绝并升级告警

---

## 三、待做 — 其他领域

- [ ] **T9. 演示与提交（M7）** — 多故障剧本跑通 / 端到端录屏 / README 复现步骤+架构图 / 性能数据
- [ ] **M5. 知识库 + 学习闭环** — sqlite-vec + bge-small(CPU) 检索 / `write_runbook` 回写 + 人工审核
- [ ] **M6 补** — 集群状态嵌 Grafana / 前端 admin 模板美化
- [ ] **环境** — 切 docker-compose（Apache Hadoop + Prometheus + Alertmanager + Grafana），可复现供评委

---

## 待敲定参数（设计决策，非实现阻塞）
- `MAX_ATTEMPTS` / `cooldown` / 观察窗口 `WINDOW`（建议 2 次 / 30–60s / 10min）
- 管理页面是否首版必须（可后补）
- 通知 webhook（飞书/钉钉，可选）
- 审批超时（现 10min）/ 巡检周期（现 5min）/ `-t` 线程（现 16，EPYC 128 线程可试 32）

## 其他遗留（仅记录）
- 集群环境：临时 CDH 6.3.2（176/177/178）→ docker-compose
- CM API 单角色操作不支持（v30 仅整服务 commands/restart），recover 按服务单位执行
- MTP 未生效（GGUF 含层未配 draft），功能不影响
