# Backup & Restore — Design

> Database backup/restore with cross-instance migration and upgrade support.

Last updated: 2026-04-13 | Status: Draft | Branch: feat/backup-restore

---

## Use Cases

| # | Use Case | Description |
|---|----------|-------------|
| UC-1 | **Corruption recovery** | Restore a database that has become corrupted or lost. Same instance, same encryption key on disk. |
| UC-2 | **Cross-instance migration** | Move all projects, credentials, clusters, and configuration from one Forge instance to another. No synchronisation — full replace. |
| UC-3 | **Breaking upgrade** | Take a pre-upgrade snapshot, upgrade the software, restore data into the new schema via Alembic forward migration. |

## Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| BR-D1 | **Re-encrypt on export** — backup archive includes the Fernet encryption key wrapped with a user-provided passphrase (PBKDF2 key derivation). On restore, the wrapped key is unwrapped and replaces the target instance's key. | Avoids the backup itself containing a raw key. Passphrase-protected archives are portable. Key replacement (not column re-encryption) is sufficient for full-replace semantics. |
| BR-D2 | **Scope: DB + encryption key** — OpenTofu state, workspaces, Helm charts, and module catalog are out of scope for v1. | Covers all three use cases. OT state is only needed to continue managing _previously deployed_ infrastructure from the new instance — a secondary concern. |
| BR-D3 | **Alembic-based restore for upgrades** — restore loads the SQL dump at its original schema version, then runs `alembic upgrade head` to migrate forward. | Natural fit for PostgreSQL + Alembic. Cheaper and safer than a schema-independent logical export. |
| BR-D4 | **UI-first API surface** — backup/restore exposed as admin-only API endpoints consumed by the frontend. No CLI tooling in v1. | Simplest path. Admin-only (`require_admin` dependency). CLI can wrap the same endpoints later. |
| BR-D5 | **Maintenance mode during restore** — the system enters a read-only maintenance mode with a user-visible message while a restore is in progress. | Prevents concurrent writes during a destructive full-replace operation. |

## Tech Debt

| ID | Item | Notes |
|----|------|-------|
| BR-TD1 | **Schema-independent logical export (JSON/YAML)** | For radical schema changes that Alembic can't bridge, a service-layer export/import decoupled from the DB schema. Record as future work — only needed if a migration is truly breaking beyond Alembic's ability. |
| BR-TD2 | **Include OT state + workspaces in backup** | Extend the archive to include `/app/state` and `/app/workspaces` volumes for full infrastructure continuity. |
| BR-TD3 | **CLI wrapper for backup/restore** | Wrap the API endpoints in a `make backup` / `make restore` command for headless/scripted use. |

## Architecture

### Backup Archive Format

The archive is a `.tar.gz` containing:

```
bnkforge-backup-{timestamp}.tar.gz
├── metadata.json          # version, alembic head, timestamp, table counts
├── dump.sql.gz            # pg_dump --format=plain | gzip
└── wrapped_key.enc        # Fernet key encrypted with passphrase-derived key
```

**Extensibility:** Additional entries (e.g., `state/`, `workspaces/`) can be added in future versions. The `metadata.json` includes a `format_version` field so the restore path can handle both old and new archive layouts.

### metadata.json Schema

```json
{
  "format_version": 1,
  "forge_version": "2.x.y",
  "alembic_head": "a3f9c2e1b847",
  "created_at": "2026-04-13T12:00:00Z",
  "table_counts": {
    "projects": 12,
    "kubernetes_clusters": 5,
    "ssh_credentials": 8
  },
  "includes": ["database", "encryption_key"]
}
```

The `includes` array declares what's in the archive. Future versions can add `"opentofu_state"`, `"workspaces"`, etc.

### Key Wrapping

1. User provides a passphrase at export time
2. Derive a 32-byte key from passphrase using PBKDF2-HMAC-SHA256 (100k iterations, random salt)
3. Encrypt the raw Fernet key bytes with AES-256-GCM using the derived key
4. Store `{salt, nonce, ciphertext, tag}` in `wrapped_key.enc` (JSON)
5. On restore, user provides passphrase → derive key → unwrap → replace `/app/keys/encryption.key`

### Export Flow

```
UI: Admin clicks "Create Backup" → enters passphrase
 │
 ▼
POST /api/system/backup  {passphrase: "..."}
 │
 ├─ 1. Run pg_dump via subprocess (same pattern as existing postgres-backup sidecar)
 ├─ 2. Read /app/keys/encryption.key
 ├─ 3. Wrap key with passphrase (PBKDF2 + AES-256-GCM)
 ├─ 4. Build metadata.json (query alembic_version table + table counts)
 ├─ 5. Package into .tar.gz
 ▼
 StreamingResponse → browser downloads archive
```

### Import Flow

```
UI: Admin clicks "Restore" → uploads archive → enters passphrase
 │
 ▼
POST /api/system/restore  {file: archive.tar.gz, passphrase: "..."}
 │
 ├─ 1. Validate archive structure (metadata.json present, format_version supported)
 ├─ 2. Unwrap encryption key with passphrase (fail fast if wrong passphrase)
 ├─ 3. Validate metadata (forge version compatibility check — warn, don't block)
 ├─ 4. Enter maintenance mode (set flag, reject non-system requests)
 ├─ 5. Drop + recreate database (or pg_restore --clean)
 ├─ 6. Load dump.sql.gz via pg_restore / psql
  ├─ 7. If alembic head in archive < current code head: run `alembic upgrade head`
  ├─ 8. Replace /app/keys/encryption.key with unwrapped key
  ├─ 9. Return success response to client (maintenance mode stays active)
  ├─ 10. Trigger sys.exit(0) — Docker Compose restarts container
  │     → On restart: new Fernet key loaded, Redis maintenance key cleared
  │     → Frontend polls /api/system/health, sees healthy, prompts refresh
  │
  ▼
  UI shows result, polls /api/system/health, prompts page refresh
```

### Maintenance Mode

- Redis key (`bnkforge:maintenance_mode`) — multi-worker safe, checked by `MaintenanceMiddleware`
- While active, all non-system API requests return `503 Service Unavailable` with body: `{"detail": "System restore in progress. Please wait."}`
- The `/api/system/health`, `/api/system/maintenance`, and `/api/system/restore` endpoints remain accessible
- Safety timeout: Redis key TTL of 10 minutes (guard against a crashed restore leaving the system locked)
- On container restart (post-restore), the startup hook clears the Redis maintenance key
- Frontend polls `/api/system/health` and shows a banner when maintenance mode is detected

### Encrypted Columns Inventory

The following models contain `*_encrypted` columns that depend on the Fernet key:

| Model | Encrypted Fields |
|-------|-----------------|
| `BareMetalHost` | bmc_username/password, ipmi_username/password |
| `User` (system.py) | Various: aws_secret_access_key, aws_session_token, aws_sso_*, gcp_credentials, azure_credentials, ssh_password/key/passphrase, credentials |
| `CloudCredentialTemplate` | credentials_encrypted |
| `SSHCredential` | password, private_key, key_passphrase |
| `Project` | cloud_credentials, encryption_passphrase, openbao_token, ssh_password/key |
| `ProjectSecret` | file_content, value |
| `Environment` | aws_secret_access_key, gcp_credentials_json, azure_credentials_json |
| `ProjectEncryptionConfig` | encryption_passphrase, openbao_token |
| `ProjectInfraConfig` | ssh_password, ssh_key |
| `ProjectCloudConfig` | cloud_credentials |
| `KubernetesCluster` | kubeconfig |
| `ModuleSource` | auth_token |
| `DiscoveryJob` | ssh_password, ssh_key |
| `DiscoveredNode` | ssh_password, ssh_key |
| `ApplicationSetting` | is_encrypted flag |

All use `core.encryption.fernet_cipher` (single Fernet key). Key replacement at restore time covers all of them.

### Security Considerations

- Backup archives are high-value secrets (contain a wrapped encryption key + all DB data)
- Passphrase strength is the user's responsibility; consider enforcing minimum length (12+ chars)
- The archive should NOT be stored on the server after download — it's a one-time download
- Audit log entries for backup creation and restore attempts (success/failure)
- Restore is destructive — the UI should require explicit confirmation

### Technical Decisions

Architect review findings addressed here.

**C1 — pg_dump access**
Add `postgresql-client-16` to the backend Dockerfile (`apt-get install -y postgresql-client-16`). The backend calls `pg_dump` / `psql` directly via subprocess — same pattern as other subprocess tools (`sshpass`, `ipmitool`). No sidecar orchestration needed.

**C2 — Maintenance mode storage**
Redis key `bnkforge:maintenance_mode` (not an in-process flag). Multi-worker safe: any backend worker can check the same Redis key. The key carries a TTL of 10 minutes as a safety net against a crashed restore leaving the system locked indefinitely.

**C3 — Fernet cipher reload via auto-restart**
After writing the new `/app/keys/encryption.key`, the backend calls `sys.exit(0)`. Docker Compose `restart: unless-stopped` restarts the container automatically. The new process loads the fresh Fernet key on startup. The startup hook also clears the Redis maintenance mode key. The frontend polls `/api/system/health`; when it sees the service come back healthy, it prompts the user to refresh.

**C4 — Upload handling**
FastAPI `UploadFile` streams to a `SpooledTemporaryFile` which spills to disk above 1 MB. No risk of OOM for large archives — the file is never fully buffered in memory.

**C5 — Alembic revision comparison**
`metadata.json` stores the actual Alembic revision hash (e.g. `"a3f9c2e1b847"`), not the migration filename. On restore, use `alembic.script.ScriptDirectory.walk_revisions()` to determine whether forward migration is needed and which revisions will be applied.

### Pydantic Schemas

```python
# Request models
class BackupCreateRequest(BaseModel):
    passphrase: str = Field(..., min_length=12, description="Passphrase to protect the backup archive")

class RestoreRequest:
    # Multipart form: file (UploadFile) + passphrase (str, Form field)
    pass  # Implemented via FastAPI UploadFile + Form parameters

# Response models
class BackupStatusResponse(BaseModel):
    in_progress: bool
    operation: str | None = None  # "backup" | "restore" | None
    started_at: str | None = None
    message: str | None = None

class RestoreResponse(BaseModel):
    status: str  # "success" | "error"
    tables_restored: int
    migrations_applied: list[str]
    warnings: list[str]
    restart_triggered: bool  # always True on success

class MaintenanceStatusResponse(BaseModel):
    maintenance_mode: bool
    message: str | None = None
    started_at: str | None = None
```

### Error Codes

| Error Code | HTTP Status | Trigger |
|------------|-------------|---------|
| `INVALID_PASSPHRASE` | 400 | Passphrase cannot unwrap the encryption key |
| `INCOMPATIBLE_FORMAT` | 400 | Archive format_version not supported |
| `INVALID_ARCHIVE` | 400 | Missing metadata.json or corrupt structure |
| `RESTORE_IN_PROGRESS` | 409 | Another backup/restore operation is running |
| `BACKUP_IN_PROGRESS` | 409 | Another backup/restore operation is running |
| `DUMP_FAILED` | 500 | pg_dump subprocess returned non-zero |
| `RESTORE_FAILED` | 500 | psql restore subprocess returned non-zero |
| `MIGRATION_FAILED` | 500 | Alembic upgrade head failed after restore |

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/system/backup` | admin | Create backup archive (streaming download) |
| `POST` | `/api/system/restore` | admin | Upload + restore backup archive |
| `GET` | `/api/system/backup/status` | admin | Check if a backup/restore is in progress |
| `GET` | `/api/system/maintenance` | public | Check maintenance mode status (no auth — needed while system is restoring) |

These fit naturally under the existing `/api/system` router which already has `dependencies=[Depends(require_admin)]`. The maintenance endpoint is explicitly exempted from that dependency.

---

## Implementation Plan

### Phase 0: Foundation (~0.5 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-000 | Pydantic schemas in `schemas/backup.py` | None | S |
| BR-001 | Error codes in `core/errors.py` | None | XS |
| BR-002 | Add `postgresql-client-16` to Dockerfile | None | XS |

### Phase 1: Key Wrapping (~0.5 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-010 | `wrap_fernet_key()` in `core/encryption.py` | None | S |
| BR-011 | `unwrap_fernet_key()` in `core/encryption.py` | BR-010 | S |
| BR-012 | Unit tests for key wrapping | BR-010, BR-011 | S |

### Phase 2: Maintenance Mode (~0.5 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-020 | `core/maintenance.py` — Redis-based flag | None | S |
| BR-021 | `MaintenanceMiddleware` | BR-020 | M |
| BR-022 | `GET /api/system/maintenance` endpoint | BR-020 | XS |
| BR-023 | Add `maintenance_mode` to `SystemHealthResponse` | BR-022 | XS |
| BR-024 | Startup hook to clear maintenance flag on boot | BR-020 | XS |
| BR-025 | Tests for maintenance mode | BR-020, BR-021 | S |

### Phase 3: Backup Service (~1 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-030 | `services/backup_service.py` — `BackupService` class | BR-000 | M |
| BR-031 | `create_backup()` — pg_dump + archive build + key wrapping | BR-030, BR-010 | L |
| BR-032 | `get_backup_status()` | BR-030 | S |
| BR-033 | `POST /api/system/backup` route | BR-031 | S |
| BR-034 | `GET /api/system/backup/status` route | BR-032 | XS |
| BR-035 | Tests for backup | BR-033 | M |

### Phase 4: Restore Service (~1.5 days)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-040 | Archive validation (`validate_archive()`) | BR-000 | M |
| BR-041 | `restore_database()` — psql restore | BR-040 | L |
| BR-042 | Alembic migration check + upgrade | BR-041 | M |
| BR-043 | Key replacement + auto-restart trigger | BR-011 | M |
| BR-044 | Full `restore_backup()` orchestration | BR-020, BR-040-043 | L |
| BR-045 | `POST /api/system/restore` route with UploadFile | BR-044 | M |
| BR-046 | Tests for restore | BR-045 | L |

### Phase 5: Frontend (~1 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-050 | TS types (`src/types/backup.ts`) | BR-000 | S |
| BR-051 | API functions (`src/lib/api/backup.ts`) | BR-050 | S |
| BR-052 | React Query hooks (`src/hooks/useBackup.ts`) | BR-051 | S |
| BR-053 | BackupPanel component (Settings > Backup & Restore) | BR-052 | M |
| BR-054 | Maintenance mode banner in AppShell | BR-023 | S |
| BR-055 | Frontend tests | BR-053, BR-054 | M |

### Phase 6: Polish & Audit (~0.5 day)

| ID | Task | Deps | Size |
|----|------|------|------|
| BR-060 | Audit log entries for backup/restore | BR-033, BR-045 | S |
| BR-061 | Passphrase strength validation (min 12 chars) | BR-031, BR-044 | XS |
| BR-062 | Update USER_GUIDE.md | BR-055 | S |
| BR-063 | Update API_REFERENCE.md | BR-034, BR-045 | S |
