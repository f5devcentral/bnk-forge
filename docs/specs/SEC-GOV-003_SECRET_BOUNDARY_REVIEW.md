# SEC-GOV-003: Secret Boundary Review

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Review where secrets, kubeconfigs, tokens, passwords, and sensitive outputs may leak across API responses, logs, error payloads, MCP responses, and background tasks.

---

## Secret Types in BNK-Forge

| Secret Type | Storage | Encryption |
|-------------|---------|------------|
| Cloud credentials (AWS keys, GCP service accounts) | Database (projects table) | Fernet encryption at rest |
| Kubeconfigs | Database (k8s_clusters table) | Fernet encryption at rest |
| SSH private keys | Database (ssh_credentials table) | Fernet encryption at rest |
| Git tokens | Database (app_settings table) | Fernet encryption at rest |
| JWT signing key | Environment variable | Not stored in DB |
| Database password | Environment variable / .env | Not stored in DB |
| Redis password | Environment variable / .env | Not stored in DB |
| Project secrets | Database (project_secrets table) | Fernet encryption at rest |
| Terraform state | Filesystem (workspace_data/) | May contain resource passwords |

---

## Properly Protected Areas

### 1. Credential Storage ✅
- All credentials encrypted at rest using Fernet symmetric encryption
- Encryption key stored in `secrets/encryption.key` (mounted read-only)
- `DecryptionError` raised if key mismatch — never returns raw encrypted data

### 2. API Response Filtering ✅
- Cloud credentials: API returns credential ID and type, NOT the actual keys
- Kubeconfigs: API returns cluster endpoint and name, NOT the kubeconfig content
- SSH keys: API returns key name and fingerprint, NOT the private key
- Project secrets: API returns secret name and metadata, NOT the value
- Git tokens: Validated against placeholder list, NOT echoed back

### 3. MCP Tool Responses ✅
- MCP observability proxy explicitly skips argument/payload logging
- Tool responses return operational data (status, health), not raw credentials
- `invocation_id` logged without request parameters

### 4. Audit Middleware ✅
- Does NOT log request bodies (only method, path, status, duration)
- JWT token decoded for username only, token itself not stored
- IP address and user agent stored for accountability

---

## Potential Leak Points

### HIGH — Terraform State Files

**Risk:** Terraform state (`*.tfstate`) may contain plaintext passwords, connection strings, and resource attributes.

**Location:** `workspace_data/` directory mounted in backend and worker containers.

**Mitigation:**
- State files are in Docker volumes, not exposed via API
- State viewer endpoint (`/api/projects/{id}/modules/{mid}/state`) filters sensitive attributes
- Remote state backends (S3) use encryption at rest

**Recommendation:** Audit state viewer output filtering for completeness. Ensure `sensitive = true` Terraform outputs are masked.

### MEDIUM — Error Payloads

**Risk:** Exception messages may include credential fragments when operations fail.

**Examples:**
- `ServiceError("kubernetes", f"K8s API error: {e.reason}")` — may include auth details in K8s API errors
- `subprocess.CalledProcessError` stderr — may contain environment variables or credential paths
- `ConnectionError` messages — may include URLs with embedded tokens

**Mitigation:**
- `handle_exception()` truncates stderr to 500 chars
- General exception handler hides error type in production (DEBUG only)
- `request_id` in error response enables log lookup without exposing details

**Recommendation:** Scrub common secret patterns (Bearer tokens, AWS keys, private key headers) from error detail strings before including in API responses.

### MEDIUM — Log Statements

**Risk:** Some log statements may include sensitive context.

**Known patterns:**
- `logger.info(f"Using token: {token}")` — NOT found in codebase (good)
- `logger.error(f"Failed to connect: {e}")` — exception messages may include credentials
- `logger.debug(...)` — debug-level logs may be more verbose than intended

**Mitigation:**
- JSON log formatter does not include request bodies
- Structured logging uses `extra={}` fields, not f-string interpolation of secrets
- Production log level is INFO (debug messages not emitted)

**Recommendation:** Add log scrubbing filter that masks patterns matching `AKIA[A-Z0-9]{16}` (AWS keys), `-----BEGIN.*PRIVATE KEY-----`, and `Bearer [a-zA-Z0-9._-]+` before writing to stdout.

### LOW — Docker Container Environment

**Risk:** Environment variables contain secrets (DB password, Redis password, JWT key).

**Mitigation:**
- `.env` file is in `.gitignore`
- Docker Compose uses `env_file` directive
- No endpoint exposes environment variables

**Recommendation:** No action needed. Standard Docker security practices.

### LOW — Nginx Access Logs

**Risk:** Nginx logs include full URL paths but NOT request bodies or authorization headers.

**Mitigation:**
- Standard nginx log format excludes Authorization header
- No sensitive data in URL paths (IDs only, not tokens)

**Recommendation:** No action needed.

---

## Boundary Summary

| Boundary | Status | Risk | Action Needed |
|----------|--------|------|---------------|
| Credential API responses | ✅ Protected | — | None |
| Kubeconfig API responses | ✅ Protected | — | None |
| SSH key API responses | ✅ Protected | — | None |
| Project secret API responses | ✅ Protected | — | None |
| MCP tool responses | ✅ Protected | — | None |
| Audit log records | ✅ Protected | — | None |
| Terraform state files | ⚠️ Partially | HIGH | Audit state viewer output masking |
| Error payloads | ⚠️ Partially | MEDIUM | Add secret scrubbing to error formatter |
| Log statements | ⚠️ Partially | MEDIUM | Add log scrubbing filter |
| Docker environment | ✅ Protected | LOW | None |
| Nginx logs | ✅ Protected | LOW | None |

---

## Recommended Actions

### Priority 1: Error Payload Scrubbing
Add a `scrub_secrets()` function to `core/errors.py` that masks known secret patterns in error messages before including them in API responses.

### Priority 2: State Viewer Audit
Review the state viewer endpoint to ensure all `sensitive = true` Terraform outputs and known secret resource attributes are masked.

### Priority 3: Log Scrubbing Filter
Add a logging filter that detects and masks AWS access keys, private key headers, and Bearer tokens in log output.
