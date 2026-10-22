# 🛡️ AEGIS

### Agentic Enhancement via Guided Instrumentation for Side-Channel Hardening

> 🚧 **Work in progress** — We are still optimizing and organizing the codebase. More components will be released here incrementally.
>
> 🧪 **Research artifact preview** · Binary firmware hardening guided by agentic reasoning and real leakage measurements.

AEGIS explores how to strengthen embedded cryptographic firmware **without needing its source code**. An LLM agent reasons over the binary, proposes safe instrumentation sites and protection payloads, then uses physical side-channel feedback to refine what it keeps. The method is described in the accompanying IEEE conference manuscript, *AEGIS: Agentic Enhancement via Guided Instrumentation for Side-Channel Hardening*.

```text
  📦 Firmware binary
       │
       ▼
  🔍 Locate active crypto  →  🎯 Race safe insertion sites
       │                                │
       └──────────────┬─────────────────┘
                      ▼
          🧠 Propose / tune payload
                      ▼
          📈 Measure leakage (TVLA)
                      ▼
          🔁 Keep best / refine / expand
                      ▼
          🛡️ Protected firmware
```

## ⚡ What AEGIS does

| Stage | Idea |
| --- | --- |
| 🔍 **Locate** | Trace the active cryptographic operation in a binary and validate its execution window with hardware triggers. |
| 🎯 **Select** | Rank leakage candidates, probe safe hook addresses, and compare them through physical measurements. |
| 🧠 **Optimize** | Generate assembly payloads and choose between tuning, strengthening, rewriting, rollback, and expansion using historical feedback. |
| 📈 **Evaluate** | Use first-order TVLA and multiple measurement views to score and retain the best valid configuration. |
| 🛡️ **Deploy** | Integrate the selected payload; the paper's trusted-deployment workflow uses authorized signing where applicable. |

The paper calls the search **RL-inspired**: it uses a feedback-driven state machine, *not* a separately trained RL policy or updates to the LLM's weights.

## 🧬 Evaluation at a glance

The manuscript evaluates five implementations across AES, CLEFIA and ML-KEM, including an HTTPS-request scenario. It reports an **81.61% average reduction in peak absolute TVLA statistic**, **6.27% average flash overhead**, and **$5.98 average LLM API cost per case**. These are paper-reported figures; the measurement datasets are not bundled with this repository.

## 🗂️ Repository map

```text
AEGIS/
├── cases/
│   └── clefia-128/        # 🔬 STM32F415 case: firmware, capture and TVLA tools
├── pipline/               # 🧩 Selected reusable analysis helpers
│   └── pipline_v13.py     # ⏳ Empty placeholder for the core workflow
├── LICENSE               # ⚖️ MIT
└── README.md
```

👉 Start with [the CLEFIA-128 case guide](cases/clefia-128/README.md) for the available source code and standalone checks. The `pipline` spelling is retained from the research workspace.

## 🚧 Release status

- ✅ Auxiliary source and one self-contained case are available.
- ⏳ `pipline/pipline_v13.py` is deliberately **empty** in this release. The end-to-end autonomous AEGIS workflow is therefore **not runnable** from this repository yet.
- 📭 Raw traces, experimental results, firmware binaries, local paths, credentials and machine-specific configuration are excluded.

## ⚖️ License & research use

The included code is released under the [MIT License](LICENSE). External tools and dependencies keep their own licenses. This repository is a source-only preview accompanying the manuscript; use the paper for the method and reported experimental results.
