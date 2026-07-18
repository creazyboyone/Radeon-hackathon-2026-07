# AIOps Agent — Autonomous Big Data Cluster Operations

An LLM-powered autonomous operations agent for big data platforms (Hadoop/CDH), built on AMD Radeon GPU + ROCm. It implements a 24/7 closed-loop: auto-inspection → alert-driven diagnosis → guardrailed remediation → verification.

## Architecture

```
Orchestrator (master session, persistent rule-based scheduler)
  ├── /auto inspection (ReAct agent, 15 iterations)
  └── /fix remediation (alert-driven preemption, ReAct agent, 15 iterations)
         ├── Tool Layer: CM API + SSH (8 tools)
         │   ├── Read-only: get_service_status / get_alerts / get_metrics / read_logs / search_kb / hdfs_admin
         │   └── Mutating: restart_service (CM API) / edit_remote_config (backup→edit→reload)
         ├── Safety Guardrail (§21 dual-axis four-tier autonomy):
         │   ├── Axis 1 AUTONOMY: supervised (human approval) / autonomous (unattended)
         │   ├── Axis 2 tier: low / medium / recover / reversible / irreversible
         │   ├── Classification: risk_rules DB (UI-configurable) + classify() + fail-closed
         │   ├── recover: attempt throttle (audit_log-derived) + circuit breaker (cross-session)
         │   ├── reversible: backup .bak.<ts> before edit, reload after
         │   └── irreversible: immediate reject + escalate alert in autonomous mode
         └── Event Bus → WebSocket → Web Console (real-time ReAct loop)

LLM: llama.cpp server (Qwopus3.6-27B-v2-MTP Q4_K_M, ROCm gfx1100, 128k context)
     KV q8_0 + Flash Attention + MTP speculative decoding (n_max=1, 37.5 t/s, +30%)
DB: SQLite (sessions/events/cluster_state/audit_log/approvals/risk_rules)
Web: FastAPI + WebSocket (backend) / React + Vite + Ant Design (frontend)
```

## Quick Start

### 1. Prerequisites

**Backend:**
```bash
pip install -r requirements.txt
```

**Frontend:**
```bash
cd web && npm install
```

**Dependencies:**
- Python 3.10+
- Node.js 18+
- An AMD Radeon GPU instance with ROCm support
- A Hadoop/CDH cluster with Cloudera Manager API access

### 2. Remote Inference Server (AMD Radeon GPU)

Upload and run the bootstrap script to set up the inference server:
```bash
scp -P <PORT> scripts/bootstrap.sh root@<REMOTE_IP>:/workspace/
ssh -p <PORT> root@<REMOTE_IP> "sed -i 's/\r$//' /workspace/bootstrap.sh && bash /workspace/bootstrap.sh"
```

The bootstrap script automatically:
1. Installs and starts SSH server
2. Downloads the model via ModelScope (~16GB)
3. Compiles/verifies llama.cpp with ROCm/HIPBLAS
4. Starts llama-server with MTP speculative decoding

Create an SSH tunnel for local access:
```bash
ssh -o ServerAliveInterval=30 -L 18080:127.0.0.1:8080 -p <PORT> root@<REMOTE_IP> -N
```

### 3. Configuration

Create `src/secrets_local.py` (not committed, copy from `secrets_example.py`):
```python
LLM_API_KEY = "<your_api_key>"
HADOOP_PASSWORD = "<your_password>"
```

Key settings in `src/config.py` (defaults shown):
```python
LLM_BASE_URL = "http://127.0.0.1:18080/v1"
LLM_MODEL = "/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf"
CLUSTER_NODES = ["hadoop01", "hadoop02", "hadoop03"]
AUTONOMY = "supervised"  # or "autonomous" for unattended mode
```

### 4. Launch

```bash
# Backend (API + WebSocket + orchestrator inspection loop)
python main.py

# Frontend (separate terminal)
cd web && npm run dev
# Open http://localhost:3000
```

## Modules

| Module | Status | Description |
|--------|--------|-------------|
| M1 Inference | ✅ | llama.cpp ROCm/HIPBLAS, Qwen27B Q4_K_M, MTP speculative decoding |
| M2 Tool Layer | ✅ | CM API + SSH, 8 tools (6 read-only + restart_service + edit_remote_config) |
| M3 Orchestration | ✅ | Persistent orchestrator + /auto inspection + /fix preemption + SQLite persistence |
| §21 Safety Guardrail | ✅ | Dual-axis four-tier (AUTONOMY × tier) + risk_rules DB + classify + attempt throttle |
| M5 KB Retrieval | TODO | sqlite-vec + bge-small |
| M6 Web Console | ✅ | FastAPI+WebSocket backend, React+Vite+AntDesign frontend |
| M7 Demo & Submission | TODO | Screen recording + performance data |

## AMD Radeon GPU Inference Optimization

| Optimization | Value | Effect |
|-------------|-------|--------|
| Model | Qwopus3.6-27B-v2-MTP Q4_K_M | 16GB, 27B params, 248K vocab |
| KV cache | q8_0 quantization | ~50% VRAM savings |
| Flash Attention | ON | Long-sequence acceleration |
| MTP Speculative Decoding | `--spec-type draft-mtp --spec-draft-n-max 1` | 37.5 t/s (baseline 28.9, +30%) |
| Prompt cache | ON (8192 MiB) | Repeated prompt acceleration |
| Context | 131072 (128k) | Long-context support |
| VRAM | 21.7G / 51.5G | Ample headroom |

### MTP Benchmark

| Config | t/s (client) | Speedup | Accept Rate |
|--------|-------------|---------|-------------|
| baseline (no MTP) | 28.9 | — | — |
| **n_max=1 (optimal)** | **37.5** | **+30%** | 77.4% |
| n_max=2 | 34.2 | +18% | 61.0% |
| n_max=3 | 34.0 | +18% | 53.7% |
| n_max=5 | 31.7 | +10% | 44.6% |
| n_max=8 | 29.5 | +2% | 24.0% |

## Safety Guardrail (§21 Dual-Axis Four-Tier)

| Tier | Typical Ops | Autonomous Behavior | Supervised Behavior |
|------|------------|---------------------|---------------------|
| low / medium | Read-only / restart non-core | Auto-execute | Auto-execute |
| recover | Restart DOWN service | Attempt throttle → auto-execute | Wait for approval |
| reversible | Config edit (backup first) | Auto-execute (forced backup) | Wait for approval |
| irreversible | hdfs format / rm | **Immediate reject + escalate** | Wait for approval |

Classification authority belongs to rules (DB), not the model. Fail-closed: unknown tools default to irreversible + not auto-executable.

## Web Console

- **Sidebar**: Agent Activity / Approval Center / Risk Rules (collapsible)
- **Agent Activity**: Session tree (master→auto/fix) + Timeline event stream (Markdown rendering + streaming output + collapsible JSON)
- **Approval Center**: Pending list + approve/reject + risk tags + badge counter
- **Risk Rules**: risk_rules CRUD (irreversible tier disables autonomous checkbox)
- **Cluster Status**: Service health cards (overall_health + per-service) when master session selected

## File Structure

```
src/
├── agent.py          # ReAct agent (inspection/remediation, streaming output)
├── orchestrator.py   # Persistent orchestrator (master session, /auto+/fix)
├── llm_client.py     # LLM client (chat + chat_stream SSE)
├── tools.py          # 8 tools (CM API + SSH)
├── guardrails.py     # Safety guardrail (§21 dual-axis four-tier + classify + throttle)
├── db.py             # SQLite Store (sessions/events/audit/approvals/risk_rules)
├── config.py         # Configuration (reads secrets from secrets_local.py)
└── web/
    ├── app.py        # FastAPI (REST API + WebSocket + risk_rules CRUD)
    └── event_bus.py # Event bus (queue.Queue bridges sync/async)
web/                  # React + Vite + Ant Design
├── src/
│   ├── App.tsx       # Layout (Sider+Header+Breadcrumb+Content)
│   ├── App.css      # Global styles + Markdown rendering
│   └── components/
│       ├── AgentActivity.tsx  # Agent activity console
│       ├── ApprovalCenter.tsx # Approval center
│       └── RiskRules.tsx      # Risk rules management
main.py               # Entry point (FastAPI thread + orchestrator main thread)
scripts/bootstrap.sh   # Remote inference server setup
scripts/mtp_bench.py   # MTP benchmark script
docs/DESIGN.md         # Detailed design doc (§21 safety guardrail)
docs/TODO.md           # Project progress overview
docs/README.md         # Project README (Chinese)
docs/README_EN.md      # Project README (English, this file)
```

## End-to-End Verification

| Round | Fault | Diagnosis | Remediation | Verification |
|-------|-------|-----------|--------------|---------------|
| 1 | DataNode stopped | ✅ 15-iteration ReAct | ❌ JAVA_HOME missing | — |
| 2 | NameNode killed (SIGTERM) | ✅ logs→KB→rule-out-OOM→jps | ✅ CM API commands/start | ✅ hdfs_admin report |