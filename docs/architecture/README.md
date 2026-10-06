# BNK Forge Architecture

> **Current state:** CI-gated multi-phase suite | Python-defined BNK modules + library sync | 3 execution engines

This directory contains architecture decisions, technical design docs, and the roadmap for BNK Forge v2.

## Active Documents

**Document status legend:**
- **Current** — matches the active product architecture and roadmap
- **Historical** — useful context from earlier phases; may describe superseded designs
- **Superseded** — retained for traceability; replaced by newer decisions

| Document | Description |
|---|---|
| [**../PRODUCT_VISION.md**](../PRODUCT_VISION.md) | *(Current)* Product direction and strategic priorities |
| [**../ROADMAP.md**](../ROADMAP.md) | *(Current)* Platform roadmap and milestones |
| [CUSTOMER_PRODUCT_VISION.md](./CUSTOMER_PRODUCT_VISION.md) | *(Historical)* Original product direction analysis |

## Architecture Overview


```
┌──────────────────────────────────────────────────────────────────────┐
│  BNK-FORGE (Control Plane)                                          │
│  Docker Compose: postgres, redis, backend, celery, frontend, proxy  │
│                                                                     │
│  ┌──────────┐ ┌────────┐ ┌──────────────────────────────────────┐  │
│  │ FastAPI   │ │ React  │ │ Engines                              │  │
│  │ API + WS  │ │ UI     │ │ ├─ OpenTofu     (local, cloud infra) │  │
│  │           │ │        │ │ ├─ K8s Direct   (remote, via kube)   │  │
│  │           │ │        │ │ └─ Operator     (remote, via WS)     │  │
│  └──────────┘ └────────┘ └──────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────┬───────────────────┬───────────────────────┘
                          │                   │
          MODEL A: Direct (primary)           MODEL B: Operator (optional)
          (BNK Forge reaches out              (phones home via WS —
           via kubeconfig)                     for outbound-only envs)
```

Three engines, two connectivity models, same UI and modules.
Kubeconfig-first (Direct) is the primary path — see Decision D3.
