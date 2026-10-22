# 🛡️ AEGIS

### Agentic Enhancement via Guided Instrumentation for Side-Channel Hardening

> 🚧 **Work in progress** — We are still optimizing and organizing the research materials. This preview contains an AES-128 firmware image only; more materials will be released here incrementally.
>
> 🧪 **Research artifact preview** · Binary firmware hardening guided by agentic reasoning and real leakage measurements.

AEGIS explores how to strengthen embedded cryptographic firmware **without needing its source code**. An LLM agent reasons over the binary, proposes safe instrumentation sites and protection payloads, then uses physical side-channel feedback to refine what it keeps. The method is described in the accompanying IEEE conference manuscript, *AEGIS: Agentic Enhancement via Guided Instrumentation for Side-Channel Hardening*.

```text
  📦 Firmware binary
       │
       ▼
  🔍 Locate active crypto  →  🎯 Rank safe insertion sites
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
│   └── aes-128/
│       └── target.bin     # 🔐 AES-128 firmware image
├── LICENSE               # ⚖️ MIT
└── README.md
```

👉 [Download the AES-128 firmware image](cases/aes-128/target.bin) · 12,480 bytes · SHA-256 `2f6dd45c2da2a3842e2257418d0725f6b719e16886106cdf4fe4fda24656e397`

## 🚧 Release status

- ✅ The AES-128 firmware image is available.
- ⏳ Source code and the end-to-end AEGIS workflow are still being organized and are not included in this preview.
- 📭 Raw traces, experimental results, local paths, credentials and machine-specific configuration are excluded.

## ⚖️ License & research use

The included artifact is released under the [MIT License](LICENSE). This repository accompanies the manuscript; use the paper for the method and reported experimental results.
