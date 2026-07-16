# AIOps-Agent

> AMD AI DevMaster Hackathon — Track 2 Agentic AI
> Autonomous Operations Agent for Big Data Platform (Hadoop Ecosystem)

## Overview

An autonomous operations agent powered by AMD Radeon GPU + ROCm local inference:

- **Continuous Inspection**: Periodic health checks of core cluster services
- **Alert-Driven Remediation**: Auto-trigger /fix diagnosis and repair on service anomalies
- **ReAct Loop**: reasoning + tool use + memory + task execution
- **Context Engineering**: One-shot context + DB state passing, avoiding long-context degradation

## Project Structure

```
Radeon-hackathon/
├── src/                # Core source code
│   ├── config.py       # Cluster/LLM/service mapping config (config-driven)
│   ├── llm_client.py   # LLM HTTP client (llama.cpp server, tool-calling)
│   ├── db.py           # SQLite storage (sessions/events/cluster_state)
│   ├── agent.py        # ReActAgent (ReAct loop + tool calling)
│   ├── orchestrator.py # Orchestrator (master scheduler: inspect/preempt/fix)
│   └── tools.py        # Tool layer (SSH + CM API: status/alerts/logs/metrics/restart)
├── main.py             # Entry point
├── bench/              # Inference benchmark tests
├── scripts/            # Operations scripts
├── docs/               # Documentation
├── tests/              # Tests
├── requirements.txt
└── .gitignore
```

## Quick Start

### Prerequisites

- SSH passwordless access to cluster nodes (3 Hadoop nodes)
- llama.cpp server running (SSH tunnel `127.0.0.1:18080` -> remote 8080)
- Python 3.10+, `pip install -r requirements.txt`

### Run

```bash
python main.py
```

The agent will continuously inspect the cluster and auto-trigger diagnosis/repair on alerts.

### Configuration

All cluster connection info is in `src/config.py`, overridable via environment variables:

```bash
export LLM_BASE_URL=http://127.0.0.1:18080/v1
export CLUSTER_BACKEND=cdh          # or apache (docker, TBD)
export CM_HOST=192.168.6.178       # Cloudera Manager address
```

Switching cluster environments only requires changing `CLUSTER_BACKEND` + `CLUSTER_NODES` + `SERVICE_MAP`.

## Progress

| Milestone | Status |
|---|---|
| M1 Inference Base | ✅ Done |
| M2 Tool Layer + ReAct | ✅ Done |
| M3 Orchestration Loop | ✅ Done |
| M4 Safety Guardrails | ⬜ TODO |
| M5 KB + Learning Loop | ⬜ TODO |
| M6 Web Console | ⬜ TODO |
| M7 Demo & Submission | ⬜ TODO |

See `docs/DESIGN.md` for full design document.
