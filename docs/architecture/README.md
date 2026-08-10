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
| [**../UX_ROADMAP.md**](../UX_ROADMAP.md) | *(Current)* UX direction and implementation status |
| [**../ENGINEERING_IMPROVEMENTS.md**](../ENGINEERING_IMPROVEMENTS.md) | *(Current)* Reliability and technical debt workstream |
| [CUSTOMER_PRODUCT_VISION.md](./CUSTOMER_PRODUCT_VISION.md) | *(Historical)* Original product direction analysis |

## Archive (completed planning docs)

Historical documents that drove the initial build (Weeks 1-8). Work is complete.

| Document | Description |
|---|---|
| [archive/ARCHITECTURE_REVIEW.md](./archive/ARCHITECTURE_REVIEW.md) | Original codebase analysis (Feb 15) |
| [archive/HYBRID_ENGINE_DESIGN.md](./archive/HYBRID_ENGINE_DESIGN.md) | kr8s engine design (implemented) |
| [archive/IMPLEMENTATION_PLAN.md](./archive/IMPLEMENTATION_PLAN.md) | Phased implementation plan (executed) |
| [archive/DEVELOPMENT_STRATEGY.md](./archive/DEVELOPMENT_STRATEGY.md) | Branching, testing, migration strategy |
| [archive/AWS_AUDIT.md](./archive/AWS_AUDIT.md) | AWS assumptions audit (fixes applied in Sprint 5-6) |
| [archive/QUICK_WINS.md](./archive/QUICK_WINS.md) | 17 quick wins (all completed in Week 1) |

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
