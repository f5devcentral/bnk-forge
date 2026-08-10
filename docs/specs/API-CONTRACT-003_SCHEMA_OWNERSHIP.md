# API-CONTRACT-003: Schema Ownership Convention

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Reduce ambiguity between inline route schemas and shared schema modules. Define when to use inline Pydantic models vs shared models, and where public contracts live.

---

## Schema Locations

| Location | Purpose | Example |
|----------|---------|---------|
| `backend/models/*.py` | SQLAlchemy ORM models (database schema) | `Project`, `Module`, `AuditLog` |
| `backend/schemas/*.py` | Shared Pydantic request/response models | `CreateProjectRequest`, `ProjectResponse` |
| Route files (inline) | One-off request models tightly coupled to a single route | `DeployAllRequest` in `project_orchestration.py` |

---

## Ownership Rules

### Rule 1: Shared Models for Tier 1 Endpoints

All Tier 1 endpoints (auth, clusters, fleet, BNK health, connectivity) MUST use shared Pydantic models in `backend/schemas/`.

**Why:** Tier 1 contracts are consumed by MCP tools, frontend, and potentially external integrations. Schema changes must be visible in code review.

### Rule 2: Inline Models Allowed for Internal Routes

Routes that are only consumed by the frontend and are not Tier 1 MAY use inline Pydantic models defined in the route file.

**When inline is OK:**
- The model is used by exactly one route
- The model is a simple request body (3-5 fields)
- The route is not consumed by MCP tools

**When inline is NOT OK:**
- The model is reused across routes
- The model represents a public API contract
- The model is consumed by MCP tools

### Rule 3: Response Models Required for Tier 1

Tier 1 endpoints MUST declare `response_model=` in the FastAPI route decorator.

```python
# Good — explicit response contract
@router.get("/api/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, db: Session = Depends(get_db)):
    ...

# Bad — returns raw dict, no contract
@router.get("/api/projects/{project_id}")
async def get_project(project_id: int, db: Session = Depends(get_db)):
    return {"id": project.id, "name": project.name}
```

### Rule 4: ORM Models Are Not Response Models

Never return SQLAlchemy ORM instances directly. Always convert to Pydantic response models or dicts.

```python
# Bad — leaks internal fields, no contract control
return project  # SQLAlchemy model

# Good — explicit field selection
return ProjectResponse.model_validate(project)
```

---

## Naming Convention

| Type | Pattern | Example |
|------|---------|---------|
| Create request | `Create{Resource}Request` | `CreateProjectRequest` |
| Update request | `Update{Resource}Request` | `UpdateProjectRequest` |
| Response | `{Resource}Response` | `ProjectResponse` |
| List response | `{Resource}ListResponse` | `ProjectListResponse` |
| Action request | `{Action}{Resource}Request` | `DeployAllRequest` |

---

## Migration Path

### Current State
Most routes return raw dicts or SQLAlchemy models. Shared schemas exist for some domains (projects, modules) but not all.

### Target State
1. All Tier 1 endpoints have shared response models
2. Tier 2 endpoints have at least inline response models
3. Request validation uses Pydantic models (not manual dict parsing)

### Steps
1. Create `backend/schemas/` directory structure mirroring route domains
2. Start with Tier 1 endpoints (per API-CONTRACT-002 plan)
3. Add response_model= to route decorators
4. Use CI check to enforce response_model on Tier 1 routes
