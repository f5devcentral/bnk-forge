# REPO-AUTH-001 — Secure Git Source Authentication Strategy Beyond PATs

## Executive Summary

Forge should move from **PAT-centric Git access** to a **provider-native, service-to-service credential model** built around **short-lived, least-privilege credentials** wherever the Git platform supports them.

**Recommended future-state defaults:**
- **GitHub private repos:** prefer **GitHub App installation tokens**
- **GitLab private repos:** prefer **project/group access tokens** for HTTPS service access, or **deploy keys** for read-only SSH where operationally simpler
- **Internal/self-hosted Git:** prefer **read-only SSH deploy keys** or **provider-native service/integration accounts** where the platform supports scoped non-human credentials
- **Public repos:** prefer **no credential at all** unless rate limits, private mirrors, or internal network controls require authenticated access

**Policy position:**
- **PATs and personal tokens are migration-only / fallback-only**, not the long-term default
- **OAuth should remain an explicit supported choice**, but not the default recommendation where stronger non-human/provider-native options exist
- **Token-in-URL cloning must be retired**
- Forge should store **credential references plus encrypted secret material only when necessary**, with explicit credential type, scope, expiry metadata, and audit history
- Source sync, remote version checks, and import flows should all use **one coherent credential policy**, even if transport differs by provider

This aligns with already-decided architecture:
- external repos are the source of truth
- Forge remains the governed control plane
- admin-managed / allowlisted sources only
- imported/synced content becomes governed catalog releases, not arbitrary live-Git execution

## 1) Decision

### Recommended policy

Forge should adopt this auth hierarchy:

1. **Provider-native app/integration credentials with short-lived tokens**
   - Best for GitHub and any platform with a true app/integration model
2. **Non-human scoped service credentials**
   - GitLab project/group access tokens
   - provider-native service accounts on self-hosted platforms
3. **Read-only SSH deploy keys**
   - Strong fit for internal/self-hosted Git and many read-only import scenarios
4. **OAuth user tokens**
   - Explicitly supported as an operator choice, but recommended mainly where the provider lacks a better service-to-service model or the workflow genuinely requires user-consented access
5. **PATs / personal tokens**
   - Migration-only, exception-based, explicitly discouraged as a default

### Why

This ordering best satisfies the required dimensions:
- minimizes personal-identity coupling
- narrows scope to repo/project/group
- supports cleaner rotation and expiry
- reduces secret reuse across systems
- improves auditability and offboarding
- fits Forge’s headless admin-managed sync model better than user-bound credentials

## 2) Recommended Defaults Matrix by Scenario

| Scenario | Preferred Default | Acceptable Fallbacks | Discouraged / Legacy-Only |
|---|---|---|---|
| **GitHub public** | Anonymous HTTPS fetch | GitHub App if rate-limited/private mirror constraints exist; read-only SSH deploy key for internal mirror workflows | PATs, OAuth user tokens |
| **GitHub private** | **GitHub App installation token** | Read-only deploy key (single repo); fine-grained PAT (temporary migration); OAuth token only for narrowly justified user-consent flows | Classic PATs; long-lived personal tokens |
| **GitLab public** | Anonymous HTTPS fetch | Deploy token or deploy key if operational policy requires authenticated fetches | PATs, OAuth user tokens |
| **GitLab private** | **Project access token** (repo/project scope) or **group access token** when multi-repo scope is required | Read-only deploy key; deploy token where repo/package read behavior fits; OAuth token only for justified user-scoped cases | Personal access tokens as default |
| **Internal/self-hosted Git** | **Read-only SSH deploy key** or **provider-native service/integration credential** | Service account HTTPS token; per-repo deploy key; internal OAuth/OIDC token only if the platform supports true non-human flow cleanly | Shared personal tokens; static broad-scope PATs |
| **Multi-repo admin-managed import estate** | Provider-native app/integration or group-scoped service credential | Multiple per-repo deploy keys where segmentation is desired | One shared human PAT across all sources |

## 3) Comparative Evaluation of Candidate Patterns

### 3.1 GitHub App installation tokens

**Verdict:** Best default for GitHub private sources

**Least privilege**
- Strong
- Repo-level installation scoping is clean
- Not tied to a human account
- Can separate Forge access from operator personal identities

**Rotation and expiry**
- Strong
- Installation tokens are short-lived by design
- Private key rotation is manageable and explicit

**Operational fit for Forge**
- Strong
- Excellent for headless sync/version-check/import workflows
- Good fit for admin-managed allowlisted sources

**Provider compatibility**
- Excellent on GitHub
- Not portable outside GitHub

**Private network / VPN fit**
- Works if Forge can reach GitHub Enterprise / GitHub endpoint from its runtime network

**Secret hygiene and auditability**
- Strong
- Better audit trail than PATs
- Avoids storing user tokens as the primary auth artifact

**Recommendation**
- **Preferred GitHub private-repo method**
- Also preferred for GitHub Enterprise if available

### 3.2 OAuth app / user tokens

**Verdict:** Supported choice, but not a Forge default

**Least privilege**
- Medium to weak
- Often user-scoped
- Tends to inherit human identity and broader accessible-repo surface

**Rotation and expiry**
- Medium
- Access tokens may expire, but refresh-token handling adds lifecycle complexity
- Offboarding becomes messy if tied to a real user

**Operational fit for Forge**
- Weak to medium
- Poor fit for a headless admin-managed service
- Better for user-driven “connect my repo” products than Forge’s governed-source model

**Provider compatibility**
- Broadly available
- But often not the best service credential model

**Private network / VPN fit**
- Depends on provider and internal IdP setup

**Secret hygiene and auditability**
- Better than classic PATs in some cases, but still user-bound and operationally awkward

**Recommendation**
- Use only when:
  - no better service/integration model exists, and
  - a real user-consent flow is required by policy
- Keep it available as an explicit source-auth choice for organizations that prefer OAuth governance.
- **Not recommended as the default source-sync credential model**

### 3.3 Deploy keys / SSH keys

**Verdict:** Strong option, especially for internal/self-hosted Git and read-only per-repo access

**Least privilege**
- Strong when per-repo and read-only
- Weakens if one key is reused broadly

**Rotation and expiry**
- Medium
- Rotation is operationally manageable but often manual
- Lacks natural short-lived expiry unless paired with higher-level automation

**Operational fit for Forge**
- Strong for read-only clone/fetch
- Simpler than app models on many internal Git systems
- Less ideal when provider APIs for metadata/version checks also require separate auth

**Provider compatibility**
- Broad
- Works well across GitHub, GitLab, Gitea, Bitbucket Server, GitLab self-managed, and generic Git over SSH

**Private network / VPN fit**
- Excellent
- Often the best fit for internal-only hosts reachable over VPN/private routing

**Secret hygiene and auditability**
- Good if:
  - keys are per-source or per-scope
  - passphrase handling is deliberate if used
  - host key verification is enforced
- Auditability is weaker than app-installation events unless the Git platform logs deploy-key usage well

**Recommendation**
- **Preferred default for many internal/self-hosted Git scenarios**
- Strong fallback for GitHub/GitLab when app/service tokens are unavailable or overly complex
- Must be **read-only**, **per-source or per-repo where practical**, and paired with strict host verification

### 3.4 GitHub fine-grained PATs

**Verdict:** Transitional fallback, significantly better than classic PATs, but still not preferred

**Least privilege**
- Better than classic PATs
- Can be narrowed by repo and permissions
- Still commonly tied to a personal identity

**Rotation and expiry**
- Better than classic PATs
- Expiry is manageable
- Still long-lived compared with installation tokens

**Operational fit for Forge**
- Operationally easy
- Good migration bridge from existing PAT-centric behavior

**Provider compatibility**
- GitHub-specific

**Private network / VPN fit**
- Works where HTTPS access works

**Secret hygiene and auditability**
- Better than classic PATs, but still suffers from human-account coupling

**Recommendation**
- **Allowed only as temporary migration fallback**
- Not the recommended steady-state default for GitHub private repos

### 3.5 GitHub classic PATs

**Verdict:** Legacy-only, discouraged

**Least privilege**
- Weak
- Historically broad scope
- Human-identity coupled

**Rotation and expiry**
- Weak to medium
- Often long-lived and forgotten

**Operational fit for Forge**
- Easy but unsafe
- Encourages over-broad static secrets

**Provider compatibility**
- GitHub only

**Private network / VPN fit**
- Works technically, but wrong operational default

**Secret hygiene and auditability**
- Weak
- Harder to justify under service-account discipline

**Recommendation**
- **Discouraged / legacy-only**
- Support only during migration with explicit sunset plan

### 3.6 GitLab project/group access tokens

**Verdict:** Best GitLab service-token default

**Least privilege**
- Strong
- Project token is best for single-project access
- Group token is acceptable when Forge must read a bounded set of repos under one group

**Rotation and expiry**
- Stronger than personal tokens
- Expiry/rotation policies are manageable

**Operational fit for Forge**
- Strong
- Good fit for admin-managed headless sync/import

**Provider compatibility**
- Excellent for GitLab cloud and self-managed GitLab

**Private network / VPN fit**
- Strong if Forge can reach the internal GitLab endpoint

**Secret hygiene and auditability**
- Better than PATs
- Clearer service ownership and offboarding than user tokens

**Recommendation**
- **Preferred default for GitLab private repos over personal tokens**
- Choose project token by default; use group token only when multi-repo access is truly needed

### 3.7 GitLab deploy tokens

**Verdict:** Useful narrow fallback, but not universal default

**Least privilege**
- Good when limited to repository/package read use cases
- Narrower than user PATs

**Rotation and expiry**
- Reasonable
- Static secret model still remains

**Operational fit for Forge**
- Good for read-only fetch scenarios
- May be less flexible than project/group access tokens for broader API-driven metadata needs

**Provider compatibility**
- Good on GitLab

**Private network / VPN fit**
- Good if endpoint reachable

**Secret hygiene and auditability**
- Better than personal tokens, but less expressive than richer integration/service models

**Recommendation**
- Acceptable fallback for read-only import/sync
- Prefer project/group access tokens when Forge needs a broader but still governed service identity

### 3.8 GitLab OAuth tokens

**Verdict:** Supported choice, but not preferred default

**Least privilege**
- Medium
- Often user-bound

**Rotation and expiry**
- Medium
- Refresh flows add complexity

**Operational fit for Forge**
- Weaker than project/group access tokens for a headless service

**Provider compatibility**
- Available, but not preferred for this product model

**Private network / VPN fit**
- Depends on internal auth topology

**Secret hygiene and auditability**
- Better than unmanaged PATs in some cases, but still user-flow oriented

**Recommendation**
- Keep available as an explicit choice where GitLab OAuth aligns with organizational controls.
- Not the preferred default for a headless service when project/group tokens are available.

### 3.9 Personal access tokens (generic)

**Verdict:** Migration-only / exception-only

**Least privilege**
- Usually weaker than non-human service credentials
- Frequently tied to a real person

**Rotation and expiry**
- Variable, but commonly poor in practice

**Operational fit for Forge**
- Easy but governance-poor

**Provider compatibility**
- Broad, which is exactly why they are often overused

**Private network / VPN fit**
- Usually works, but policy should not be based on convenience

**Secret hygiene and auditability**
- Weak compared with service credentials or deploy keys

**Recommendation**
- **Not a default anywhere**
- Allow only as bounded migration fallback or for platforms with no safer alternative

### 3.10 Internal/self-hosted SSH deploy keys

**Verdict:** Preferred default for many internal Git estates

**Least privilege**
- Strong when per-repo and read-only

**Rotation and expiry**
- Medium
- Needs operational rotation discipline

**Operational fit for Forge**
- Strong
- Avoids needing user-bound HTTPS tokens
- Very compatible with VPN/private network routing realities

**Provider compatibility**
- Excellent across heterogeneous self-hosted Git

**Private network / VPN fit**
- Excellent

**Secret hygiene and auditability**
- Good if managed as a dedicated Forge machine credential with strict host-key policy

**Recommendation**
- **Preferred for internal/self-hosted Git when no strong app/integration model exists**
- Should be first-class in the Forge credential model

### 3.11 Service account tokens

**Verdict:** Preferred where the platform provides true non-human service identities

**Least privilege**
- Strong if scope can be repo/project bounded

**Rotation and expiry**
- Medium to strong depending on provider support

**Operational fit for Forge**
- Strong
- Good for internal enterprise Git platforms

**Provider compatibility**
- Platform-dependent

**Private network / VPN fit**
- Strong

**Secret hygiene and auditability**
- Better than personal tokens if the account is non-human, dedicated, and policy-controlled

**Recommendation**
- **Preferred over PATs for internal/self-hosted HTTPS auth**
- Should be treated as a top-tier option wherever available

### 3.12 Provider-native app/integration credentials

**Verdict:** Best general principle where available

**Least privilege**
- Usually strongest

**Rotation and expiry**
- Usually strongest

**Operational fit for Forge**
- Excellent for headless controlled sync

**Provider compatibility**
- Varies by platform

**Private network / VPN fit**
- Good if Forge can reach the service endpoint

**Secret hygiene and auditability**
- Usually best-in-class

**Recommendation**
- Forge policy should explicitly prefer these over user tokens whenever the provider supports them well

## 4) Scenario-Specific Recommended Defaults

### 4.1 GitHub public/private

#### GitHub public
**Default**
- Anonymous HTTPS clone/fetch

**Use credential only when needed**
- GitHub App for rate limits, governance, or enterprise mirror constraints
- SSH deploy key only if the source is actually an internal mirror or requires controlled SSH access

#### GitHub private
**Default**
- **GitHub App installation token**

**Fallbacks**
1. Read-only SSH deploy key for a single repo or tightly bounded repo set
2. Fine-grained PAT as migration bridge
3. OAuth token only for exceptional user-consent workflows

**Discouraged**
- Classic PATs
- Broad personal PATs used across multiple repos
- Any token embedded in clone URLs or persisted in plaintext config

### 4.2 GitLab public/private

#### GitLab public
**Default**
- Anonymous HTTPS clone/fetch

**Fallbacks**
- Deploy token or deploy key if authenticated access is required by policy

#### GitLab private
**Default**
- **Project access token**
- Use **group access token** only when one Forge source intentionally spans multiple repos in the same governed group

**Fallbacks**
1. Read-only deploy key
2. Deploy token
3. OAuth token for justified user-flow exceptions

**Discouraged**
- Personal access tokens as standard operating model
- One group-wide high-privilege token shared across unrelated import domains

### 4.3 Internal / self-hosted Git

**Default**
- **Read-only SSH deploy key** for per-repo or narrowly bounded access
- If the platform supports it well, **provider-native service/integration credential** is equally acceptable and may be preferable for centralized lifecycle control

**Fallbacks**
1. Dedicated non-human service account HTTPS token
2. Internal OIDC/OAuth token only if it is truly service-oriented and operationally durable
3. Personal token only as temporary migration exception

**Discouraged**
- Shared engineer accounts
- Shared PATs copied between sources
- Password-based Git auth
- Broad machine users with write privileges when Forge only needs read/import access

## 5) Preferred / Fallback / Discouraged Guidance

### Preferred
- GitHub App installation tokens
- GitLab project access tokens
- GitLab group access tokens only when justified by real multi-repo need
- Read-only SSH deploy keys
- Provider-native service/integration credentials
- Dedicated service account tokens for internal HTTPS auth where app/integration support is absent

### Acceptable Supported Alternatives / Fallbacks
- GitLab deploy tokens
- GitHub fine-grained PATs
- OAuth tokens as an explicit supported operator choice, especially where a user-consent or org-mandated OAuth flow is preferred
- Internal service-account HTTPS tokens

### Discouraged / Legacy-Only
- GitHub classic PATs
- Personal access tokens as steady-state defaults
- Shared human-owned tokens across multiple sources
- Token-in-URL cloning
- Storing reusable tokens in global settings without scope metadata and expiry governance
- Write-capable repo credentials when Forge only needs read/import access

## 6) Forge Credential Model

Forge should stop modeling source auth as a simplistic `token vs none` concept. The model should support:

### 6.1 Credential type taxonomy
Store credential metadata with an explicit auth class such as:
- `none`
- `github_app`
- `gitlab_project_token`
- `gitlab_group_token`
- `gitlab_deploy_token`
- `oauth_token`
- `ssh_deploy_key`
- `service_account_token`
- `legacy_pat`

This is conceptual planning guidance, not a schema mandate, but the future model must be extensible enough to avoid another auth redesign.

### 6.2 What Forge should store conceptually
Per source credential record:
- credential type
- provider / host
- scope target (repo, group, host, installation, project)
- read/write capability declaration
- expiry/rotation metadata
- last validation result
- last used timestamp
- audit trail references
- encrypted secret material only when necessary
- reference to keypair/private key or app credential material where applicable

### 6.3 Direct storage vs referenced storage
**Prefer direct encrypted storage only for credentials Forge must actively use** for unattended sync.
Where external secret managers are introduced later, Forge should support secret-reference indirection, but this is not required to define the policy.

### 6.4 Source vs global credential scope
Forge should prefer:
- **source-scoped credentials** by default
- **provider/org/group-scoped credentials** only when multiple approved sources intentionally share the same access boundary

Avoid a single catch-all global Git token as the long-term model.

## 7) Storage, Rotation, and Audit Model

### 7.1 Storage expectations
- Encrypt secrets at rest
- Never store raw tokens in plaintext settings
- Never embed tokens in persisted URLs
- Never expose secrets back through API read surfaces after creation/update
- Maintain host key data or trust policy for SSH sources

### 7.2 Runtime handling rules
- Do not inject tokens into logged clone URLs
- Do not allow tokens in subprocess command lines where they may appear in process lists
- Prefer authenticated headers, environment passing, ephemeral credential files, or SSH agent/keyfile approaches over URL mutation
- Mask secrets in logs, exceptions, task output, and audit payloads

### 7.3 Rotation policy
- Short-lived credentials should be renewed automatically where the provider supports it
- Static credentials must carry:
  - created_at
  - expires_at if available
  - last_rotated_at
  - rotation_owner / provenance
- Forge should warn before expiry and fail clearly after expiry
- Rotation should be possible without recreating the source definition

### 7.4 Audit expectations
Audit events should exist for:
- credential created
- credential updated/rotated
- credential validated/tested
- source sync attempted
- auth failure during sync/version check
- credential disabled/revoked
- source switched from legacy PAT to preferred method

Audit payloads must capture:
- source identifier
- provider/host
- credential type
- actor
- result
- failure class
- no secret material

## 8) Operational Connectivity Assumptions

Forge should explicitly assume:
- it must reach the Git endpoint from the runtime environment that performs sync/import/version-check work
- DNS, TLS trust, firewall, and proxy/VPN requirements must be satisfied from the Forge backend/worker network path, not just from an admin laptop
- private/internal Git is supported only when Forge’s runtime has actual routed access

### Fail-early operational checks
Source validation should distinguish:
- DNS/connectivity failure
- TLS trust failure
- SSH host-key trust failure
- auth failure
- authorization/scope failure
- repo not found / path mismatch

The product should fail early with actionable messages such as:
- Forge cannot reach host from runtime network
- SSH host key is untrusted/mismatched
- credential valid but lacks repo read scope
- private repo requires approved credential on this source

### VPN/private-network fit
Forge should not promise “private Git supported” unless:
- the backend/worker runtime can reach it
- outbound network path is intentional and documented
- any required VPN/bastion/private DNS assumptions are part of deployment guidance

## 9) Migration Guidance from Current PAT / Token-in-URL Behavior

Current-state evidence shows existing PAT/token-centric behavior in:
- `backend/services/module_sync_service.py` (`auth_type == 'token'`)
- `backend/routes/module_library.py` (`module_library.git_token`)
- adjacent clone/version-check logic that still relies on token injection patterns

### Migration direction
Forge should migrate in stages:

#### Phase 1 — coexistence with explicit deprecation
- Keep current token-based records working temporarily
- Reclassify them as `legacy_pat` / legacy token credentials
- Add admin-visible warnings that PAT/token auth is deprecated
- Stop treating PATs as the default path for new source setup

#### Phase 2 — preferred-method onboarding
- New source creation flow should guide admins toward:
  - GitHub App
  - GitLab project/group token
  - SSH deploy key
  - internal service credential
- PAT entry should be behind “legacy / fallback” guidance

#### Phase 3 — runtime hardening
- Remove token-in-URL handling from normal flows
- Move all runtime auth to safer transport patterns
- Unify sync and version-check auth resolution so both use the same credential policy

#### Phase 4 — sunset
- Disable creation of new classic PAT-based credentials
- Optionally block new personal-token sources entirely except under explicit admin override
- Leave read-only compatibility window for existing records until migrated

### Recommended policy on compatibility window
- Maintain temporary compatibility only long enough to migrate existing sources
- Planning assumption: this should be a **bounded deprecation period**, not open-ended indefinite support

## 10) Bounded Implementation Follow-Ons

These are planning slices, not code tasks yet.

### REPO-AUTH-002 — Credential model and source-auth schema normalization
Scope:
- define extensible credential-type model
- separate source credential metadata from legacy global token settings
- represent scope, expiry, capability, and validation status cleanly

Acceptance focus:
- future auth methods fit without schema churn
- legacy PAT records can be represented and migrated

### REPO-AUTH-003 — Secure runtime transport for Git auth
Scope:
- remove token-in-URL clone behavior
- define safe credential handoff for HTTPS and SSH flows
- unify auth handling for sync and remote version checks

Acceptance focus:
- no secrets in URLs/logs/process listings
- source sync and version check follow one policy

### REPO-AUTH-004 — GitHub App support
Scope:
- GitHub App credential onboarding
- installation selection/scoping
- short-lived token acquisition flow
- validation and audit events

Acceptance focus:
- GitHub private repos can use app-based auth end-to-end

### REPO-AUTH-005 — GitLab service credential support
Scope:
- project/group access token support
- deploy token support where needed
- validation and scope reporting

Acceptance focus:
- GitLab private repo onboarding no longer assumes PATs

### REPO-AUTH-006 — SSH deploy-key support for internal/self-hosted Git
Scope:
- read-only SSH credential support
- host-key trust model
- validation/reporting for internal Git reachability

Acceptance focus:
- internal Git can be onboarded without personal tokens

### REPO-AUTH-007 — Audit, expiry, and rotation UX/policy
Scope:
- audit events
- expiry warnings
- credential test/rotate workflow
- deprecation warnings for legacy credentials

Acceptance focus:
- operators can see which sources use legacy auth and which need rotation

### REPO-AUTH-008 — Legacy PAT sunset
Scope:
- deprecate `module_library.git_token`
- migration tooling/flow for existing token-based sources
- disable new legacy credential creation after migration window

Acceptance focus:
- PAT-centric behavior is no longer the default or easiest path

## 11) Explicit Discouraged / Legacy-Only Guidance

The following should be stated plainly in the final policy:

- **Classic GitHub PATs are legacy-only**
- **Personal access tokens are not the preferred Forge model on any provider**
- **OAuth user tokens are not the default for headless admin-managed sync**
- **Token-in-URL Git cloning is prohibited in the target state**
- **One shared global Git token for all sources is discouraged**
- **Write-capable repo credentials are discouraged unless a future workflow truly requires write operations**
- **Human-owned credentials should not be the steady-state identity for Forge source sync**

## 12) Final Recommendation

Approve the following future-state direction:

1. **Adopt provider-native, non-human, least-privilege credentials as the default**
2. **Use GitHub App installation tokens as the GitHub standard**
3. **Use GitLab project/group access tokens as the GitLab standard**, with deploy keys/tokens as bounded alternatives
4. **Use read-only SSH deploy keys or service-account credentials for internal/self-hosted Git**
5. **Keep OAuth available as an explicit supported choice**, while still recommending non-human/provider-native credentials first
6. **Treat PATs and personal tokens as temporary migration fallbacks only**
7. **Retire token-in-URL behavior and unify all Git auth under one governed credential policy**
8. **Make source-scoped credential metadata, rotation, validation, and auditability first-class**

This gives Forge a secure, operationally realistic, provider-aware auth model that fits its governed-control-plane role without reopening source-boundary decisions already settled by `ARCH-EXT-001`.

## Assumptions / Open Questions

- Assumes Forge remains **read-only** against external Git sources for the planned sync/import model; if future write-back/promotion workflows are added, auth policy will need a separate write-scope review.
- Assumes no mandatory external secrets manager requirement yet; if one is introduced later, the credential model should allow secret-reference indirection.
- Open question: whether GitLab private default should be framed strictly as **project access token first** or as **project access token / deploy key dual default** depending on whether API metadata access beyond clone/fetch is required.
- Open question: whether GitHub Enterprise and self-managed GitLab instances in customer networks impose additional certificate/private-DNS onboarding requirements that deserve a separate operational runbook item.
- Open question: whether Forge will support a single provider credential intentionally shared across multiple approved sources, or require explicit per-source attachment even when the same underlying credential is reused.
