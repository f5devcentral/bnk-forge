# D-035: Docker Builds Behind Corporate DLP TLS Interception (github.com)

**Status:** DECIDED
**Date:** 2026-07-24
**Issue:** f5devcentral/bnk-forge#496
**Ref:** F5 KB57735

---

## Context & Problem Statement

The corporate DLP (rolled out to managed workstations ~2026-07-23) TLS-intercepts `github.com` traffic using the internal CA `ca.f5.goskope.com`.
During `docker build` operations, `RUN curl https://github.com/...` inside build containers fails with:

```text
curl: (60) SSL certificate problem: self-signed certificate in certificate chain
```

Build containers do not inherit the host OS trust store. This breaks from-scratch image builds on DLP-managed workstations. CI environments (GitHub-hosted runners) are unaffected.

---

## Domain Interception Matrix

We probed all external domains accessed during container image builds across the repository:

| Target Domain | Tool / Asset | Intercepted by DLP? | Resolution Strategy |
|---|---|---|---|
| **`github.com`** / `*.githubusercontent.com` | OpenTofu, llmtop, Infracost, ORAS | ✅ **YES** (`ca.f5.goskope.com`) | Use Docker `ADD` (daemon-level fetch) |
| **`get.helm.sh`** | Helm CLI | ❌ No (`DigiCert`) | Retain `curl` |
| **`dl.k8s.io`** | kubectl CLI | ❌ No (`Let's Encrypt`) | Retain `curl` |
| **`awscli.amazonaws.com`** | AWS CLI v2 | ❌ No (`Amazon Root CA`) | Retain `curl` |
| **`download.docker.com`** | Docker CE CLI | ❌ No (`GTS Root`) | Retain `curl` |
| **`pypi.org`** / `files.pythonhosted.org` | Python packages (`pip`) | ❌ No | Standard `pip install` |
| **`registry.npmjs.org`** | Node packages (`npm`) | ❌ No | Standard `npm ci` |
| **`dl-cdn.alpinelinux.org`** / Debian apt | Base OS packages | ❌ No | Standard `apt`/`apk` |

---

## Decision & Resolution Strategy

Per F5 KB57735, use Docker **`ADD`** for all `github.com` downloads during image builds:

1. **Host-Daemon Fetching:** Docker `ADD` instructs the Docker daemon on the host OS to download the remote URL. The host daemon trusts `ca.f5.goskope.com`, so TLS interception succeeds transparently.
2. **No Certificate Baking:** No corporate CA cert is copied into the Docker image, ensuring images remain clean, portable, and public/CI-safe.
3. **No Insecure Bypasses:** No `curl -k` or `--insecure` flags are used.
4. **Integrity Preserved:** Per-architecture SHA256 checksum verification (`sha256sum -c`) is retained in subsequent `RUN` commands.
5. **Selective Scope:** Only `github.com` downloads are switched to `ADD`. Non-intercepted domains (`get.helm.sh`, `dl.k8s.io`) remain on `curl`.

---

## Repo-Wide Dockerfile Audit

All 8 Dockerfiles in the monorepo were audited:

| Dockerfile Path | GitHub Downloads Present? | Action Taken |
|---|---|---|
| `backend/Dockerfile` | Yes (`opentofu`, `llmtop`) | Switched to `ADD` in `tooling-deps` stage. |
| `bnk-operator/Dockerfile` | No (`get.helm.sh` only) | Retained `curl`. |
| `mcp-server/Dockerfile` | No (`pip` only) | Unchanged. |
| `frontend-v2/Dockerfile` | No (`npm` only) | Unchanged. |
| `proxy/Dockerfile` | No (`apk` only) | Unchanged. |
| `Dockerfile.agent` | No (`pip` only) | Unchanged. |
| `tests/e2e/Dockerfile` | No (`npx` only) | Unchanged. |
| `docs/devcontainer-template/...` | No (Commented template) | Unchanged. |

---

## Verification & Acceptance

- [x] From-scratch `docker build --target tooling-deps` succeeds behind the DLP.
- [x] SHA256 checksum verification succeeds for all binaries.
- [x] `tofu`, `helm`, `kubectl`, `llmtop` binaries execute and respond to `--version`/`version`.
- [x] Working tree clean; committed as `8fb51787`.
