# ADR-494: BNK Release Management Consolidation

- **Status:** Proposed (2026-07-22)
- **Tracking:** GitHub #494
- **Builds on:** ADR-478 (per-deploy release *selection*; this branch is **stacked** on `feat/adr-478-bnk-release-selection`)
- **Related:** ADR-204 (SSH BNK layer), `docs/DPU_DEPLOY_REQUIREMENTS.md` R6.2
- **Domain language:** see `CONTEXT.md` (produced via grill-with-docs, 2026-07-21/22)

## Context

ADR-478 made the BNK release a **per-deploy selection** backed by the `bnk_deployable_release` catalog. It deliberately left *release management* — how releases are sourced, tracked across the estate, and reconciled with detection — untouched. Live testing surfaced the gaps:

- Two "release" tables with unclear roles: `bnk_deployable_release` (exact deploy recipes) vs `bnk_releases` (fuzzy GA-line detection). Operators are unsure which is the source of truth.
- Deployable releases are seeded/refreshed ad hoc; there is no first-class notion of *where a release came from* (remote registry vs air-gapped mirror), unlike the mature module-source model.
- Forge does not durably record *which release a given cluster is running*; discovery computes it and discards it.
- BNK↔DOCA are decoupled in a way that bites: `bnk_deployable_release.doca_version` is **stored but unused**; the DPU BFB/DOCA is selected independently from `bluefield_software_images`. R6.2 intends a coupling (compare installed vs target, reflash on mismatch) that was never built.

The term "release" was itself overloaded; `CONTEXT.md` now pins the language.

## Decision

Consolidate BNK release *management* around two clearly-scoped tables, a first-class release source, and durable per-cluster tracking.

1. **Two tables, distinct roles — keep both.**
   - **Catalog** (`bnk_deployable_release`) = Releases *available to deploy*, each an exact recipe. **Source of truth for deploys.**
   - **Install base** (`bnk_releases`) = everything Forge *learns* about BNK — a **superset** of the Catalog (recipe optional). Holds exact identity when known plus the **match term** (`flo_version_prefix`/ranges) used to classify installs to a **Version line**.

2. **Cluster tracking + observed upsert.** A cluster carries two links: **deployed Release → Catalog** (intent; null if not Forge-deployed) and **running Release → Install-base registry** (reality, from discovery). On discovery, if the exact running Release is not already a registry row, Forge **upserts one** (`source = observed`) so the running link always resolves — even for a version the Catalog never shipped. The **deployed-vs-running divergence is the drift signal** (realises R6.2 without the throwaway check).

3. **First-class `ReleaseSource`.** A new entity (its *own* schema — not reusing `ModuleSource`'s git/OAuth machinery), `kind = oci | mirror/proxy | manual`, with optional credentials. The Catalog syncs Releases *from* a source; Catalog rows gain provenance (`source_id`, `last_synced`) and a source-driven **sync** (generalises ADR-478's repo.f5.com online refresh). Releases are immutable, so sync only *adds*. Air-gap = point a source at a local mirror/proxy.

4. **Catalog tab in the UI.** Relocate the minimal deployable-release admin out of `BareMetalPanel` into the Catalog page, as a peer of the BlueField-image and module catalogs; surface `ReleaseSource` management + refresh there.

5. **BNK↔DOCA coupling — decided in Phase C, not before.** Today the two are independent (correctly, per the DPU-BFB-OS vs host-OS two-axis split documented in `CONTEXT.md`/ADR-478). Whether a Release should *reference* a compatible DOCA (and drive reflash) is deferred to live validation.

## Phases

- **A** (CI-testable, disconnected-first): `ReleaseSource` entity + source-driven sync + Catalog tab UI.
- **B** (estate-testable): install-base registry as superset (exact rows + observed upsert on discovery) + `Cluster.running_release_id` + drift = deployed-vs-running.
- **C** (bench-gated): BNK↔DOCA coupling decision + (if adopted) reflash-on-mismatch.

## Consequences

- "Single source of truth" is preserved *without* merging the tables: deploys read the Catalog; identity/detection reads the Install base. Merging a fuzzy classifier with an exact recipe was rejected (loses the Version-line↔Release 1-to-many and burdens every row with irrelevant columns).
- Per-project is explicitly **not** the model — a project owns many clusters; a release is a property of the cluster.
- Adds a network side-effect on catalog sync/refresh (bounded, non-blocking; airgap uses a mirror source).
- Stacks on unmerged ADR-478 → rebase if ADR-478's E2E surfaces fixes. Migration numbering continues after ADR-478's; watch the `embedded-agent-deployment` v2_142–144 collision (renumber on rebase to staging).

## Related hardening (same "unvalidated catalog metadata" theme)

- **Fixed** on the ADR-478 branch (`15557d95`): BFB download poisoned-cache, cached-BFB validation, save-time DOCA URL warnings.
- **Parked** for this work: `host_os`/`host_arch` controlled-vocabulary normalization (`amd64`↔`x86_64`, `arm64`↔`aarch64`); `reboot-host` default timeout bump for DPU-mode reboots (900s too tight — observed ~15½ min recovery).

## Phase A live-fetch — decision record (promotes backlog ADR494-001)

Phase A shipped source-driven sync over a **manually-supplied** manifest only; live pulling from a source was deferred to backlog item ADR494-001. Locked here via `grill-with-docs` (2026-07-23) so the build can proceed. Scope: for a `ReleaseSource` of `kind = oci | mirror`, list the available manifest **tags**, let the operator select one or more, pull each manifest, and upsert per-Release **Catalog** (`bnk_deployable_release`) rows.

**Empirical finding (durable — confirmed by a live pull, 2026-07-23).** The `f5-bigip-k8s-manifest` chart is a multi-release *index* by schema, but every tag pulled to date carries **exactly one** Release in its `releases:` list (verified for tag `2.2.1-3.2226.0-0.0.511`; see CONTEXT.md → *Manifest*). The registry tag is the **long full-version string** (`2.2.1-3.2226.0-0.0.511`), not a clean `2.2.1`. Consequence: the picker MAY treat **1 tag = 1 Release** for UX, but the parser MUST still iterate `releases:` as a list.

**Decisions:**

1. **Selection model (Q1).** Tag-centric: browse tags → select tag(s) → pull → populate a Releases pane → add to Catalog. Post-pull summary recovers transparency; no pre-pull preview needed.
2. **Listing/fetch mechanism (Q2).** Add **`oras`** to the backend image and shell out to `oras repo tags` for enumeration; keep `helm pull` (helm v3.20.0 already in the image) for fetching the manifest chart. Chosen over hand-rolling GAR's undocumented `/v2/.../tags/list` token dance — `oras repo tags` is the *proven* path (it enumerated the live tags), it matches Forge's existing shell-out-to-CLI pattern (helm/kubectl), and image-size cost is negligible. Trade-off: a new image dependency + reliance on the tag-list API vs. bespoke HTTP code — accepted.
3. **Auth (Q3).** Per-operation **ephemeral** login via a single `registry_session(source)` context manager (one decrypt + one login, reused across a whole batch — not per tag): decrypt `credential_encrypted` → `helm registry login -u _json_key_base64 --password-stdin <host>` writing into a **per-call temp registry config file** (`tempfile.mkdtemp()` → `<tmpdir>/config.json`); run oras/helm; `finally: shutil.rmtree(tmpdir)`. Secret fed via **stdin**, never argv.
   - **Config sharing (corrected — the "one env var both honor" assumption is false):** `helm` honors `HELM_REGISTRY_CONFIG` / `--registry-config <file>`; `oras` honors `DOCKER_CONFIG` (a *dir*) / `--registry-config <file>`. The reliable shared mechanism is to pass **`--registry-config <tmpdir>/config.json` explicitly to all three calls** (`helm registry login`, `helm pull`, `oras repo tags`). **Do NOT mutate `os.environ`** — the backend is multi-threaded (uvicorn); two concurrent syncs would clobber/leak each other's config. Unique temp dir per call + the explicit flag eliminates the collision.
   - **Reuse existing on-host login logic** at `modules/bare_metal/bnk_ssh_base.py:400-421`, which already handles the SA-key login **and two credential shapes** (bare base64 SA key with `-u _json_key_base64`, vs a pre-built dockerconfigjson containing `"auths"`) plus shred-in-finally. The fixed `-u _json_key_base64` form alone fails for pre-built-dockerconfig credentials.
   - Host per kind: `oci` → fixed `repo.f5.com`; `mirror` → host from `source.url`. `decrypt_value` raising `DecryptionError` → surface a generic "credential decryption failed" (never echo the value into `sync_error`, which is API-returned).
   - **Manifest extraction:** `helm pull` yields a `.tgz`, not YAML. Reuse the extraction at `modules/bare_metal/bnk_prerequisites.py:209-221` (`helm pull … --version <tag> --untar` → `find . -name '*manifest*.yaml' ! -name Chart.yaml`), don't hand-roll.
   - **Image (corrected):** the sync endpoints run **synchronously in the backend/uvicorn process** (not Celery), so `oras` must be COPY'd into the Dockerfile **`api` stage** (and worker stage for parity), not only `tooling-deps`.
   - *Persistence is unchanged from Phase A:* the SA key lives in `ReleaseSource.credential_encrypted`, Fernet-encrypted at rest via `core.encryption`, exactly like Forge's SSH private keys / passwords; API exposes only `has_credential`. Shared threat model: `FERNET_KEY` is the master secret for all Forge secrets. No new persistence work.
4. **Mirror semantics (Q4).** `mirror` = a pull-through proxy **or** a private registry that mirrors repo.f5.com's paths exactly. Only host + credential differ; the repo path `release/f5-bigip-k8s-manifest` is assumed identical, reusing the OCI code path. Re-hosting at a different path is out of scope (use `manual` upload instead).
5. **Degradation (Q5).** Listing is **best-effort**; pull-by-tag is the primitive. List OK → populate picker. List fails (network/auth/registry lacks tag API) → show error but keep a **manual tag-entry** box that runs the same pull. The existing **paste/upload-manifest** path remains the fully-offline third option. Three coherent add-paths: pick-listed-tag / type-known-tag / paste-manifest.
6. **Batch add (Q6).** Per-tag **savepoint** (`begin_nested`) best-effort: one tag's failure neither poisons the session nor aborts the others. Return a summary (added / skipped-already-present / failed-with-reason). Upsert is **idempotent**, keyed on `bnk_manifest_version` (Releases immutable → re-add is a no-op). Source `sync_status = success` unless the whole operation fails (login/list) — a partial batch stays `success` with per-tag detail.
7. **Picker UX (Q7).** Cross-reference listed tags against existing Catalog rows → "in Catalog" badge + disabled checkbox; sort semver-descending; pre-release tags shown+flagged, unchecked by default (not hidden); tags displayed **verbatim**. **Build-time verification step:** run a live `oras repo tags repo.f5.com/release/f5-bigip-k8s-manifest` first to settle the real tag shape (short vs long vs both) before finalizing the picker.
8. **Scheduling (Q8).** `auto_sync` **deferred** — manual "Fetch tags" / "Sync" action only. Model fields (`auto_sync`, `sync_interval_hours`) stay but the UI toggle is hidden. Rationale: auto-adding tags silently mutates the deploy source-of-truth; Releases publish rarely; revisit as its own item once manual is proven.
9. **Surfaces (implementation calls, non-domain).** Tag-picker lives in the **existing Sync dialog** ("Fetch available tags" populates the picker; manifest paste demoted to offline fallback). Backend: `GET /release-sources/{id}/tags` (best-effort list) + `POST /release-sources/{id}/tags:pull` (pull selected + upsert, per-tag savepoint + summary).

## References

- GitHub #494 · CONTEXT.md · ADR-478 · ADR-204 · `docs/DPU_DEPLOY_REQUIREMENTS.md` R6.2 · backlog ADR494-001
