# How to Write CI Container Runner Modules and Blueprints for BNK Forge

*A developer's guide to packaging ordinary containers as deployment modules, composing
them into blueprints, surfacing outputs, and (optionally) registering Kubernetes
clusters — with two worked examples.*

---

## Table of Contents

1. [What a "container runner module" is](#1-what-a-container-runner-module-is)
2. [The mental model: artifacts, modules, blueprints, projects](#2-the-mental-model)
3. [Anatomy of a container artifact manifest](#3-anatomy-of-a-container-artifact-manifest)
4. [The step model: init / plan / apply / destroy](#4-the-step-model)
5. [State and the workspace](#5-state-and-the-workspace)
6. [Inputs and credentials](#6-inputs-and-credentials)
7. [Outputs: how a module surfaces results](#7-outputs)
8. [Registering a Kubernetes cluster](#8-registering-a-kubernetes-cluster)
9. [Repository layout and registering a source in BNK Forge](#9-repository-layout-and-registration)
10. [Blueprints and releases](#10-blueprints-and-releases)
11. [The deploy / destroy lifecycle](#11-the-deploy--destroy-lifecycle)
12. [The security model](#12-the-security-model)
13. [Worked example 1 — Environment Diagnostic Scanner](#13-worked-example-1--environment-diagnostic-scanner)
14. [Worked example 2 — Deploying any `docker-compose.yml` stack](#14-worked-example-2--deploying-any-docker-composeyml-stack)
15. [Field reference (cheat sheet)](#15-field-reference-cheat-sheet)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. What a "container runner module" is

BNK Forge can deploy infrastructure in several ways. The **container engine** is the
most general: instead of running OpenTofu or Ansible, it runs **your container image**,
one or more times, as ordinary `docker run` invocations, and treats the container's
output as the module's result.

This means **any tool you can put in a container** — a CLI, a shell of helm/kubectl,
a Go binary, a Python script — becomes a first-class BNK Forge deployment module. The
container is the "CI runner": it does the work; BNK Forge orchestrates it, persists its
state, captures its outputs, retries it, and (optionally) registers any cluster it
produces.

You do **not** write any backend code. You author two JSON manifests and a container
image, push them to a git repo and an image registry, and register the source in BNK
Forge.

Use a container runner module when:

- You already have a tool/CLI that does the deployment (e.g. a vendor installer).
- The work isn't naturally expressible as Terraform/Helm.
- You want one self-contained image that carries all its own dependencies.

---

## 2. The mental model

```
  image registry (ghcr.io, …)            git "module source" repo
  ┌───────────────────────────┐          ┌──────────────────────────────┐
  │  your-runner@sha256:…      │◄──pin────│ <module>/bnkforge.artifact.json│
  │  (the CI runner image)     │          │ <module>/bnkforge.pack.json    │
  └───────────────────────────┘          │ forge-blueprint.json (optional)│
                                          └──────────────┬───────────────┘
                                                         │ sync
                                                         ▼
                                    BNK Forge: module_library + blueprint_release
                                                         │ create project from blueprint
                                                         ▼
                                    Project → modules → deploy-all
                                                         │ per module
                                                         ▼
                                    Container engine: docker run <image> <argv> …
                                       (workspace mounted, creds injected,
                                        outputs captured, cluster registered)
```

Key objects:

| Object | What it is | Where it lives |
|---|---|---|
| **Artifact manifest** (`bnkforge.artifact.json`) | The runtime contract: which image, which steps, state, inputs, cluster registration | your module source repo |
| **Pack manifest** (`bnkforge.pack.json`) | The catalog identity: module name/category/engine/form inputs | your module source repo |
| **Module source** | A git repo BNK Forge syncs to discover modules | registered in BNK Forge (`module_sources`) |
| **Module library entry** | The synced, validated module BNK Forge can deploy | `module_library` (Postgres) |
| **Blueprint** (`forge-blueprint.json`) | A composition of modules + dependency wiring + form inputs | your repo / a blueprint source |
| **Blueprint release** | A versioned, importable snapshot of a blueprint | `blueprint_releases` |
| **Project** | An instance created from a blueprint release | `projects` |

The container engine is `engine: container`. A module declares it in the pack
manifest's `deployment_pack`, and BNK Forge merges the artifact manifest's runtime
blocks (`container_image`, `steps`, `state`, `execution`, `cluster`, …) into the stored
pack manifest at sync time.

---

## 3. Anatomy of a container artifact manifest

`bnkforge.artifact.json` is the heart of a container module. Here is a fully-annotated
example; every block is explained in the following sections.

```jsonc
{
  "schema_version": 1,
  "name": "my-runner",
  "version": "1.0.0",
  "kind": "container_image",
  "description": "Procedural steps invoke the artifact's own image via argv only.",

  // The image is DIGEST-PINNED — no floating tags. This is enforced.
  "container_image": {
    "registry_host": "ghcr.io",
    "repository": "your-org/my-runner",
    "digest": "sha256:0123…(64 hex)"
  },

  // Which lifecycle operations this module supports.
  "lifecycle": {
    "supports_apply": true,
    "supports_destroy": true
  },

  // Optional: ask BNK Forge to inject a cloud credential set into the container.
  "credentials": { "cloud": "ibmcloud" },

  // Where the persistent workspace mounts, where outputs are read from, and any
  // HOME-style env the tool needs. See §5.
  "state": {
    "mount_path": "/work",
    "outputs_file": "outputs.json",
    "home_env": { "TOOL_HOME": "/work/.tool" },
    "scope": "component"
  },

  // Engine + resource limits for the container.
  "execution": {
    "engine": "container",
    "limits": { "cpus": "2", "memory": "2g", "pids": 512 }
  },

  // The work. argv-only, runs in THIS image. See §4.
  "steps": {
    "init":  [ { "name": "init",  "args": ["my-runner", "init"],  "timeout_seconds": 300, "run_once": true } ],
    "apply": [ { "name": "up",    "args": ["my-runner", "up"],    "timeout_seconds": 1800,
                 "retry": { "max_attempts": 3, "backoff_seconds": 300 } } ],
    "destroy": [ { "name": "down", "args": ["my-runner", "down"], "timeout_seconds": 1800 } ]
  },

  // Form inputs the user fills in (or that are auto-sourced). See §6.
  "inputs": {
    "required": [
      { "name": "region", "type": "string", "source": "user", "description": "Target region." }
    ],
    "optional": [
      { "name": "verbose", "type": "boolean", "source": "user", "default": false,
        "description": "Verbose logging." }
    ]
  },

  // OPTIONAL: declare how this module surfaces a cluster for registration. See §8.
  "cluster": {
    "name_output": "cluster_name",
    "api_server_output": "master_url",
    "region_output": "region",
    "cloud_provider": "ibm",
    "kubeconfig_file": ".tool/kubeconfig.yaml"
  },

  "references": []
}
```

### The pack manifest companion

Alongside the artifact manifest, each module directory carries a `bnkforge.pack.json`
that declares the module's **catalog identity** and that its engine is `container`.
The simplest reliable way to author this is to **copy an existing container module's
`bnkforge.pack.json`** and change the identity fields (`module.name`, `module.path`,
`module.version`, `module.description`, `category`, `provider`). At sync time BNK Forge
validates both files and merges the artifact's runtime blocks (`container_image`,
`steps`, `state`, `execution`, `references`, `cluster`) into the stored pack manifest —
so the artifact manifest is where the *runner contract* lives, and the pack manifest is
mostly identity + form inputs.

> **Rule of thumb:** put the deployable behavior in `bnkforge.artifact.json`; keep
> `bnkforge.pack.json` minimal and modeled on an existing module.

---

## 4. The step model

A module runs **step-sets** keyed by lifecycle phase: `init`, `plan`, `apply`,
`destroy`. Each is an ordered list of steps. A step is one `docker run` of your image.

```jsonc
{
  "name": "cluster-up",                 // label shown in logs
  "args": ["my-runner", "cluster", "up", "--region", "{{inputs.region}}"],
  "timeout_seconds": 1800,              // hard cap for THIS step
  "when": "{{inputs.provision}}",       // optional gate: skip if falsy
  "env": { "MY_FLAG": "{{inputs.verbose}}" },  // extra env (templated)
  "run_once": true,                     // see below
  "retry": { "max_attempts": 3, "backoff_seconds": 300 }
}
```

### argv-only — there is no shell

`args` is an **argv vector that runs inside your image**, with the image's entrypoint
overridden. There is **no shell, no command-string, and no image override**:

- The first token may **not** be a shell (`sh`, `bash`, …).
- The step may **not** contain `shell`, `command`, `script`, or `image` keys.
- You cannot point a step at a *different* image — every step runs *this* artifact's
  digest-pinned image.

This is a security boundary (§12). The practical consequence: **bake your logic into
the image's entrypoint/binary** and invoke it by name (`["my-runner", "up"]`), rather
than trying to `sh -c "…"`.

### Templating

`args`, `env`, and `when` are templated with `{{inputs.<name>}}` (the resolved form
inputs). `when` skips the step when the value is falsy — use it to make optional phases.

### `run_once`

A step marked `run_once: true` records a marker in the persistent workspace after its
first success and is **skipped on subsequent applies**. Use it for non-idempotent
setup (e.g. a `init` that aborts if its workspace already exists). Without this, a
re-apply would re-run setup and fail.

### `retry`

`retry: { max_attempts, backoff_seconds }` re-runs a failed step with a fixed backoff,
continuing from the persistent workspace each time. A step that exceeds its own
`timeout_seconds` (hard timeout) is **not** retried — only soft failures are. Defaults
to a single attempt.

> **Caveat — retry is blind.** It retries *any* non-timeout failure, including
> permanent ones. If your tool can fail deterministically (a bad input, a missing
> dependency), prefer to fail fast inside the container with a clear message rather
> than rely on retries that will only waste the backoff window.

### Timeouts and the worker budget

`timeout_seconds` is the per-step hard cap (a step exceeding it returns exit 124 and is
marked timed-out, not retried). For container modules, BNK Forge derives the Celery
task time-limit from the manifest's worst-case budget — `Σ(step.timeout × retry
attempts + backoffs)` — so a long, retrying build is not killed by a global worker
limit. Set realistic `timeout_seconds`; don't pad them enormously.

---

## 5. State and the workspace

Every container module gets a **persistent workspace** — a named Docker volume subpath
that survives across runs (init → apply → destroy, and re-applies). This is where your
tool keeps its state, caches, generated config, and outputs.

```jsonc
"state": {
  "mount_path": "/work",                 // where the workspace mounts in the container
  "outputs_file": "outputs.json",        // path (relative to mount_path) you write outputs to
  "home_env": { "TOOL_HOME": "/work/.tool" },  // env vars set in the container
  "scope": "component"                   // "component" (default) or "deployment"
}
```

- **`mount_path`** — the container's working directory and where the workspace volume is
  mounted. Your tool should write all state under here so it persists.
- **`home_env`** — arbitrary env vars passed to the container (`-e KEY=VALUE`). Use this
  to point a tool's HOME/config/cache at a writable workspace path (critical: a fresh
  runner's `$HOME` may be empty or non-writable — redirect caches into `mount_path`).
- **`scope`**:
  - `component` (default): each module gets its own workspace (`<project>/<module-id>`).
  - `deployment`: all modules of one blueprint deployment **share** a workspace
    (`<project>/bp-<release-id>`). Use this when phased modules must read each other's
    on-disk state (e.g. a cluster phase writes a kubeconfig that a later phase consumes).

> The workspace is the contract between phases and between BNK Forge and your tool.
> Anything you want to survive a crash, a retry, or a later phase goes in `mount_path`.

---

## 6. Inputs and credentials

### Form inputs

`inputs.required` / `inputs.optional` declare the variables a user fills in (and which
ones are auto-sourced). Each input:

```jsonc
{
  "name": "cluster_name",
  "type": "string",                 // string | boolean | number | …
  "source": "user",                 // user | project | credential_template
  "source_field": "region",         // for non-user sources, which field to pull
  "description": "Human-readable help.",
  "default": "",                     // optional default
  "sensitive": true,                 // mask in UI/logs
  "example": "my-cluster",
  "validation": { /* optional rules */ }
}
```

`source` controls where the value comes from:

- **`user`** — the deploy form. The value is templated into `args`/`env` as
  `{{inputs.<name>}}`.
- **`project`** — pulled from the project (e.g. `source_field: "region"` →
  `project.region`). The user never types it; it's resolved from project context.
- **`credential_template`** — pulled from the project's cloud credential template
  (e.g. `source_field: "ibmcloud_api_key"`). Use for secrets the platform already holds.

### Cloud credentials

`credentials: { "cloud": "ibmcloud" }` tells BNK Forge to inject the project's cloud
credential set (resolved from the credential template) into the container as
environment variables at run time. The container reads them like any env. This keeps
secrets out of your manifests and out of module outputs.

> **Never** print credentials to stdout — BNK Forge redacts known secret values from the
> streamed logs per line, but the safest path is to not emit them at all.

### Secret files (entitlement material on disk)

Many vendor CLIs want entitlement material as **files** in their working directory
rather than as env (a FAR tarball, a licence JWT, a kubeconfig). Declare them and
BNK Forge materializes the project's secrets into the run workspace before your
steps run:

```jsonc
"secret_files": [
  { "secret_name": "far_tarball", "path": "poc/keys/f5-far-auth-key.tgz" },
  { "secret_name": "jwt_token",   "path": "poc/keys/.jwt" }
]
```

- `secret_name` — the name of a **ProjectSecret** (file- or value-typed). The
  operator adds it once under the project's **Secrets** tab. (The key is
  `secret_name`, not `secret`: `secret` is on the raw-secret key denylist — that
  key is how someone would inline a secret *value*, which manifests must never do.)
- `path` — **workspace-relative** (resolved under `state.mount_path`). Absolute
  paths and `..` traversal are rejected at validation *and* re-checked at write
  time. Parent directories are created for you.
- Files are written **0600** and re-created on every run, so rotating a secret in
  the UI takes effect on the next run with no cleanup step.
- Declaring `secret_files` makes those secrets **required**: they appear in the
  project's Secrets tab, and a missing one fails the run immediately, naming the
  secret — rather than letting your CLI fail obscurely twenty minutes in.

**What persists, and for how long.** Materialized secret files live as 0600
files on the module's workspace volume for the **module's lifetime** — a
decrypted copy is at rest on disk between runs. This is a deliberate trade-off,
not an oversight: destroy runs the same materialization as apply, and vendor
CLIs (ocibnkctl included) need the entitlement files present for **destroy**
too, so they cannot be deleted after a run. Every run re-tightens the mode to
0600, and the files are removed together with the workspace when the module or
project is deleted (`workspace_manager.cleanup_module_workspace` /
`cleanup_project_workspaces`). Anyone who can read the workspace volume can
read the materialized secrets.

> **Warning — shared workspaces.** With `state.scope: "deployment"` all of a
> blueprint deployment's modules share **one** workspace directory, mounted
> into every module's container with the same uid. Any module's container can
> therefore read another module's materialized secret files. If that isolation
> matters, keep the default per-module scope (`component`) for modules that
> declare `secret_files`.

Only `kind: container_image` artifacts (which have a workspace) may declare this.

---

## 7. Outputs

A module surfaces results by writing a JSON object to its **`state.outputs_file`** (a
path inside the workspace). After a successful `apply`, BNK Forge reads that file and
stores it as the module's `outputs`. Those outputs:

1. Show up on the module in the project (the UI / API).
2. Feed **dependent modules** in a blueprint (a later module can be wired to a key an
   earlier module produced).
3. Drive **cluster registration** (§8).

```jsonc
// state.outputs_file = "outputs.json", so write /work/outputs.json:
{
  "cluster_name": "my-cluster",
  "master_url": "https://c1.example.com:31000",
  "region": "us-south",
  "anything_else": "…"
}
```

Output values are typically strings (or simple JSON). Keep them small and
non-sensitive — they are persisted in the project record. If your tool produces a large
artifact (a kubeconfig, a report), write it to a **file in the workspace** and surface
only its *path* or a small summary in the outputs (see the cluster contract below,
which reads a kubeconfig from a workspace file rather than from outputs).

---

## 8. Registering a Kubernetes cluster

If your module **provisions or connects to a Kubernetes cluster**, you can have BNK
Forge register it on the Kubernetes page automatically — generically, with no
provider-specific backend code. You declare a `cluster` block in the artifact manifest;
BNK Forge reads the kubeconfig your module surfaces and creates a managed cluster
record linked to the project.

### The contract

```jsonc
"cluster": {
  "name_output": "cluster_name",                 // which output holds the cluster name
  "api_server_output": "master_url",             // (optional) output holding the API server URL
  "region_output": "region",                     // (optional) output holding the region
  "cloud_provider": "ibm",                       // drives the credential-template refresh dispatch
  "kubeconfig_file": ".tool/kubeconfig.yaml"     // workspace-relative kubeconfig file, OR…
  // "kubeconfig_output": "kubeconfig"           // …an output key that holds the kubeconfig (YAML or base64)
}
```

How it works after a successful `apply`:

1. BNK Forge resolves the kubeconfig — from the declared **`kubeconfig_file`** (read from
   the workspace) or **`kubeconfig_output`** (read from the module outputs).
2. It resolves the cluster name (`name_output`), API server (`api_server_output`),
   region (`region_output`), and provider (`cloud_provider`) — the module *declares*
   these so registration doesn't depend on how the project was configured.
3. It normalizes + encrypts the kubeconfig, creates (or updates) a `KubernetesCluster`
   row, and **links it to the project**.
4. On `destroy`, it **unregisters** the cluster.

The whole thing is **best-effort and self-gating**: no `cluster` block (or no
kubeconfig surfaced) → no registration, and a registration failure never fails the
deploy.

### Surface a *token-based* kubeconfig, not admin certs

This is the single most important practical detail, learned the hard way:

- If the provider's API server uses a **publicly-trusted TLS cert** (e.g. IBM ROKS
  `*.containers.cloud.ibm.com`), the kubeconfig legitimately has **no
  `certificate-authority-data`** — that's normal; omit it and rely on system trust. A
  builder that *requires* a CA will fail on these clusters.
- Prefer a **token-based** kubeconfig (`users[].user.token` = a short-lived IAM/bearer
  token) over embedded admin client certs. Because the cluster row is linked to the
  project's credential template, BNK Forge can **re-mint the token** on its own
  (`refresh_kubeconfig` dispatches on `cloud_provider`), keeping the session current
  indefinitely. Admin certs are long-lived but **not** refreshed this way and expire.
- The `cloud_provider` you declare matters: it routes the refresh. For IBM, use `ibm`
  (also accepted: `ibmcloud`, `roks`). Provider strings are compared case-insensitively.

### Where the kubeconfig comes from

Your runner is responsible for *producing* the kubeconfig. Two clean options:

1. **Write it to a workspace file** and point `kubeconfig_file` at it (recommended —
   keeps the kubeconfig out of the DB `outputs`). Build it as: the cluster `server` URL,
   `certificate-authority-data` *only if present*, and a `users[].user.token`.
2. **Emit it in outputs** under a key and point `kubeconfig_output` at it. Simpler, but
   the raw kubeconfig then lives in the module outputs.

---

## 9. Repository layout and registration

A **module source** is a git repo BNK Forge syncs. Layout:

```
your-modules-repo/
├── moduleA/
│   ├── bnkforge.pack.json          # catalog identity + engine=container + inputs
│   └── bnkforge.artifact.json      # the runner contract (image, steps, state, cluster)
├── moduleB/
│   ├── bnkforge.pack.json
│   └── bnkforge.artifact.json
└── forge-blueprint.json            # (optional) a blueprint composing the modules
```

Register and sync it (operator/admin). Via API:

```bash
# Create the module source (git)
curl -sk -X POST "$BNK/api/module-sources" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-modules","source_type":"git","url":"https://github.com/you/your-modules-repo.git","branch":"main"}'

# Sync it — discovers + validates the modules, merges artifact blocks into the catalog
curl -sk -X POST "$BNK/api/module-sources/<id>/sync" -H "Authorization: Bearer $TOKEN"
```

Re-point a branch with `PUT /api/module-sources/<id> {"branch":"…"}` then sync again.
After sync, your modules appear in `module_library` and are deployable.

> Container images must be **digest-pinned** in the artifact manifest (no floating
> tags). When you publish a new image, update the digest in the manifest and re-sync.

**Validate before you sync (or in your content repo's CI).** From a bnk-forge
checkout, `scripts/validate_catalog_content.py` runs the same pack/artifact/
blueprint validators the sync runs — plus the path-equality, artifact-version
and blueprint-pin coherence checks — against a plain checkout, no running Forge
needed:

```bash
python scripts/validate_catalog_content.py /path/to/your-content-repo
```

### 9.1 Module versioning (D-033)

The catalog keeps **one immutable row per `(source, path, version)`**. Your repo
still tracks a single manifest per module directory — bump `module.version`
(and the artifact digest) in place and re-sync; the catalog *accumulates* the
history:

- **New version** in the manifest → a new catalog row. Prior versions stay
  untouched, and any project module pinned to them keeps deploying (and, more
  importantly, destroying) with the exact manifest it was created from.
- **Same version, same content** → idempotent no-op (`modules_unchanged`).
- **Same version, changed content** → the sync reports a `version_conflict`
  and never overwrites. Publishing a change requires a version bump — the same
  rule blueprint releases follow.
- `is_latest` marks the newest version per `(source, path)`; the catalog UI
  shows latest versions by default with an "All versions" toggle.

> **The rule: bump on ANY edit.** Every change to a manifest — including
> metadata-only edits (a `provider` label, a description tweak, a new tag) —
> bumps the version, in the pack manifest, the artifact manifest, and any
> blueprint pinning the module. There is no "cosmetic" exception: the catalog
> row is a content hash, so an unbumped edit is a `version_conflict` on the
> next sync and your source stays divergent from the catalog until you bump
> anyway. When the tool binary itself hasn't changed, add a packaging segment
> to the tool's version (e.g. `1.20.0` → `1.20.0.1`) — blueprint pins accept
> multi-segment versions, and the catalog orders them correctly.

Blueprints pin module versions (`modules[].version`, §10) and resolution is
**exact**: if the pinned version isn't in the active catalog, deploy is blocked
with `BLUEPRINT_MODULE_VERSION_MISSING` listing the available versions — never
silently substituted. Project modules are pinned to the version row they were
created with; upgrading is an explicit operator action
(`POST /api/project-modules/<id>/change-version {"target_version": "…"}`, or
**Change Version** in the module card menu) and takes effect on the module's
next plan/apply/destroy.

---

## 10. Blueprints and releases

A **blueprint** (`forge-blueprint.json`) composes modules into a deployable stack with
dependency ordering and a unified input form. Shape (abridged, modeled on real
blueprints):

```jsonc
{
  "schema_version": 1,
  "blueprint": {
    "id": "my-stack",
    "version": "1.0.0",
    "name": "My Stack",
    "description": "…"
  },
  "estimated_time": "20-30 minutes",
  "difficulty": "intermediate",
  "tags": ["example"],

  // The unified deploy form.
  "inputs": {
    "required": [
      { "name": "region", "type": "string", "source": "user", "description": "Region." }
    ],
    "optional": []
  },

  // The modules, with dependency wiring.
  "modules": [
    { "id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [],
      "inputs": { "region": "${region}" } },
    { "id": "app",     "module": "modules/app",     "version": "1.0.0", "depends_on": ["cluster"],
      "inputs": { "cluster_name": "${cluster.cluster_name}" } }
  ]
}
```

- `modules[].module` references a module by its catalog path; `version` pins it.
- `depends_on` builds the DAG. BNK Forge runs independent modules in parallel and waits
  on dependencies — `app` won't start until `cluster` is applied.
- `inputs` interpolation: `${region}` (a blueprint input), `${cluster.cluster_name}`
  (an *output* of the `cluster` module — this is how a downstream module consumes an
  upstream's results).

A blueprint source is synced like a module source; each version becomes a **release**
(`discovered` → `imported`). Releases are immutable, and the bump-on-any-edit
rule from §9.1 applies identically here: any change to `forge-blueprint.json` —
including re-pinning a module version or editing display metadata — bumps
`blueprint.version`, or the next sync reports the release as conflicted rather
than updating it. Import a release to make it deployable:

```bash
curl -sk -X POST "$BNK/api/blueprint-catalog/sources/<id>/sync"  -H "Authorization: Bearer $TOKEN"
curl -sk -X POST "$BNK/api/blueprint-catalog/releases/<id>/import" -H "Authorization: Bearer $TOKEN"
```

Then create a project from the release and deploy it.

---

## 11. The deploy / destroy lifecycle

```
create project from release ──► project + modules (with dependency edges)
        │
        ▼  deploy-all
   ┌─ for each ready module (deps satisfied), in parallel ─┐
   │   container engine:                                   │
   │     run init steps   (run_once markers honored)       │
   │     run apply steps  (retry/backoff, when-gates)      │
   │     read state.outputs_file → module.outputs          │
   │     if `cluster` declared → register cluster          │
   │   on success → unblock dependents (auto-advance)      │
   └──────────────────────────────────────────────────────┘

   destroy-all runs the DAG in REVERSE; on the cluster module's destroy
   the registered cluster is unregistered.
```

- Output streaming: a long step's stdout is streamed live to the task log (and over a
  WebSocket) — you can watch a 45-minute build progress instead of waiting for the end.
  Output-so-far survives a worker crash.
- Dependent modules consume upstream outputs via the blueprint's `${module.output}`
  wiring.
- Make `apply` **idempotent** (re-runnable) and `destroy` a **no-op-success when there's
  nothing to tear down** — the orchestrator may re-invoke phases, and a no-op phase that
  errors instead of succeeding will stall a reverse-order teardown.

---

## 12. The security model

The container engine is deliberately constrained:

- **Digest-pinned images only** — no floating tags; you pin `sha256:…` and update it
  deliberately.
- **argv-only steps** — no shell, no command-strings, no per-step image override. Your
  logic lives in the image; steps invoke it by argv.
- **Sibling containers via a scoped Docker socket proxy** — the worker never mounts the
  raw host socket; the runner gets a constrained subset of the Docker API.
- **Resource limits** — `execution.limits` (cpus/memory/pids) bound each run.
- **`--rm`, `no-new-privileges`, `cap-drop=ALL`** — runs are ephemeral, can't escalate,
  and hold no Linux capabilities.
- **Non-root images only — enforced, and this will reject your image if you ignore it.**
  Before a step starts, Forge reads the image's `Config.User` and **refuses to run it if
  it resolves to root** (uid 0 — *including an image that simply never declares a `USER`,
  which is the default for most base images*). The workspace is mounted from the host, so
  a root container would be a host-root write primitive. Forge does **not** silently remap
  you to another uid with `--user`: that would override your image's `USER` and break your
  own state writes. So: put a **numeric** `USER` in your Dockerfile. The gate requires a bare
  decimal uid — uid **1000** matches the workspace owner and is the safe choice. A **named**
  user such as `USER nonroot` (the distroless default) is now **refused**: a name can't be
  resolved to a uid without the image's own `/etc/passwd`, so it can't be proven non-root.
  If you were on `USER nonroot`, switch to `USER 1000` — it matches the workspace owner (chowned
  `1000:1000`), so your state writes under `mount_path` succeed. A higher uid such as `65532` clears
  the non-root gate but cannot write the host-mounted workspace.
  Forge does **not** silently remap you with `--user`. This mirrors Kubernetes `runAsNonRoot`,
  which the Kubernetes runner applies to the same artifacts.
- **A dedicated network** — steps attach to the `bnk-forge-artifacts` bridge network rather
  than the daemon's default bridge, so artifact containers don't sit alongside unrelated
  containers. Egress still works (you can reach cloud control planes); you just don't share
  a network with the rest of the host's workloads.
- **Secret redaction** — known secret values are stripped from streamed logs per line.
- **Credentials via injection** — `credentials.cloud` injects creds as env at run time;
  they don't live in manifests or outputs.

Design your runner to be a good citizen: idempotent, writes only under `mount_path`,
emits small non-sensitive outputs, and fails fast with clear messages.

---

## 13. Worked example 1 — Environment Diagnostic Scanner

**Goal:** a module that runs a container which **scans the environment it runs in**
(the workspace, env, network reachability, mounts) and writes a **diagnostic dump to a
module output**. Useful for module authors to see exactly what a container module
*sees* inside BNK Forge.

Because steps are argv-only, we bake the scanner into a thin image built **from a public
base** (`alpine`) with the scan as its entrypoint, and publish it to a public registry.

### 13.1 The scanner (entrypoint)

`scan.sh` (baked in as the image entrypoint — this runs *inside* the container, where a
shell is fine; the argv-only rule applies to BNK Forge *steps*, not to your image's
internals):

```sh
#!/bin/sh
# Diagnostic dump of the environment this container runs in. Writes JSON to $OUT.
set -eu
OUT="${OUTPUTS_PATH:-/work/outputs.json}"
mkdir -p "$(dirname "$OUT")"

# Helpers — emit JSON-safe strings.
json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# Network reachability (best-effort; do not fail the scan if these miss).
dns_ok="false";  getent hosts github.com >/dev/null 2>&1 && dns_ok="true"
proxy_ok="false"; (nc -z -w 2 docker-socket-proxy 2375 >/dev/null 2>&1) && proxy_ok="true"

# Workspace contents (the persistent mount).
ws_listing=$(ls -la /work 2>/dev/null | head -40 | tr '\n' '~')

# Non-secret env keys only (names, not values).
env_keys=$(env | cut -d= -f1 | sort | tr '\n' ' ')

cat > "$OUT" <<EOF
{
  "scanner_version": "1.0.0",
  "hostname": "$(json_str "$(hostname)")",
  "whoami": "$(json_str "$(id -un 2>/dev/null || echo unknown)")",
  "uid": "$(id -u 2>/dev/null || echo '?')",
  "cwd": "$(json_str "$(pwd)")",
  "workspace_writable": $( touch /work/.probe 2>/dev/null && { rm -f /work/.probe; echo true; } || echo false ),
  "workspace_listing": "$(json_str "$ws_listing")",
  "env_var_names": "$(json_str "$env_keys")",
  "dns_resolves": $dns_ok,
  "docker_socket_proxy_reachable": $proxy_ok,
  "kernel": "$(json_str "$(uname -a)")",
  "scanned_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "wrote diagnostic dump to $OUT"
cat "$OUT"
```

`Dockerfile` (from a public base):

```dockerfile
FROM alpine:3.20
RUN apk add --no-cache coreutils
COPY scan.sh /usr/local/bin/forge-scan
RUN chmod +x /usr/local/bin/forge-scan
# REQUIRED: declare a non-root USER. Forge refuses to start an image that runs
# as root (see §12) — and a base image with no USER *is* root. uid 1000 matches
# the workspace owner, so your state writes under mount_path work.
USER 1000
ENTRYPOINT ["/usr/local/bin/forge-scan"]
```

Build, push to a **public** package, and grab the digest:

```bash
docker build -t ghcr.io/you/forge-env-scan:1.0.0 .
docker push ghcr.io/you/forge-env-scan:1.0.0
docker buildx imagetools inspect ghcr.io/you/forge-env-scan:1.0.0 --format '{{.Manifest.Digest}}'
# → sha256:…  (paste into the artifact manifest)
```

### 13.2 The module manifests

`env-scan/bnkforge.artifact.json`:

```jsonc
{
  "schema_version": 1,
  "name": "forge-env-scan",
  "version": "1.0.0",
  "kind": "container_image",
  "description": "Scans and dumps the BNK Forge container-runner environment to an output.",
  "container_image": {
    "registry_host": "ghcr.io",
    "repository": "you/forge-env-scan",
    "digest": "sha256:…"                  // the digest you just printed
  },
  "lifecycle": { "supports_apply": true, "supports_destroy": false },
  "state": {
    "mount_path": "/work",
    "outputs_file": "outputs.json"        // the scanner writes /work/outputs.json
  },
  "execution": { "engine": "container", "limits": { "cpus": "1", "memory": "256m" } },
  "steps": {
    "apply": [
      { "name": "scan",
        "args": ["forge-scan"],           // the image entrypoint binary — argv-only, no shell
        "env": { "OUTPUTS_PATH": "/work/outputs.json" },
        "timeout_seconds": 120 }
    ]
  },
  "inputs": { "required": [], "optional": [] },
  "references": []
}
```

`env-scan/bnkforge.pack.json` — copy from an existing container module and set identity:

```jsonc
{
  "schema_version": 1,
  "module": {
    "name": "forge-env-scan",
    "path": "env-scan",
    "version": "1.0.0",
    "category": "diagnostics",
    "provider": "none",
    "description": "Diagnostic scan of the BNK Forge runner environment."
  },
  "deployment_pack": { "engine": "container" },
  "inputs": { "required": [], "optional": [] },
  "outputs": { "key_outputs": [
    { "name": "hostname", "type": "string", "description": "Runner hostname." },
    { "name": "workspace_writable", "type": "string", "description": "Whether /work is writable." },
    { "name": "docker_socket_proxy_reachable", "type": "string", "description": "Proxy reachability." }
  ] }
}
```

### 13.3 A one-module blueprint

`forge-blueprint.json`:

```jsonc
{
  "schema_version": 1,
  "blueprint": { "id": "forge-env-scan", "version": "1.0.0", "name": "Forge Env Scan",
                 "description": "Run the environment scanner as a project." },
  "inputs": { "required": [], "optional": [] },
  "modules": [
    { "id": "scan", "module": "env-scan", "version": "1.0.0", "depends_on": [], "inputs": {} }
  ]
}
```

### 13.4 Run it and read the output

```bash
# Register + sync the module source and (auto-discovered) blueprint, import the release,
# create a project from it, and deploy.
curl -sk -X POST "$BNK/api/module-sources" ...                       # register source
curl -sk -X POST "$BNK/api/module-sources/<id>/sync" ...             # sync modules + blueprint
curl -sk -X POST "$BNK/api/blueprint-catalog/releases/<rel>/import" ...
curl -sk -X POST "$BNK/api/stacks/releases/<rel>/projects" -d '{"name":"env-scan-demo", ...}'
curl -sk -X POST "$BNK/api/projects/<pid>/deploy-all" ...
```

When the `scan` module applies, BNK Forge runs the container, the scanner writes
`/work/outputs.json`, and BNK Forge captures it as the module's outputs. View them on
the module in the UI, or:

```bash
curl -sk "$BNK/api/project-modules/project/<pid>" -H "Authorization: Bearer $TOKEN"
# → the scan module's "outputs" contains hostname, workspace_writable, dns_resolves,
#   docker_socket_proxy_reachable, env_var_names, …
```

This is the smallest end-to-end container runner module: **public base image + a scan
entrypoint + an artifact manifest = a deployable diagnostic that surfaces results as
module outputs.** Use the same skeleton for any "run a tool, capture its result" module.

---

## 14. Worked example 2 — Deploying any `docker-compose.yml` stack

**Goal:** a reusable pattern for turning **any application that ships a
`docker-compose.yml`** into a BNK Forge container runner module that brings the stack up
**in the same environment as BNK Forge** — as sibling containers on the same Docker host,
on a shared Docker network. `apply` becomes `docker compose up`, `destroy` becomes
`docker compose down`, and the module surfaces the stack's endpoints as outputs.

This works because the container engine already runs your runner as a sibling container
through a **scoped Docker socket proxy**. If you give the runner the same `DOCKER_HOST`,
*it* can drive `docker compose` against the same daemon — so the compose stack lands
right next to BNK Forge.

We'll build it generically, then instantiate it with **Grafana + Prometheus**.

### 14.1 The mechanism

```
  BNK Forge worker ──run──► compose-runner (sibling container)
                                   │  DOCKER_HOST=tcp://docker-socket-proxy:2375
                                   ▼
                            docker compose up -d --wait
                                   │
                                   ▼
                 grafana + prometheus containers on the shared Docker network
                            (same host/network as BNK Forge)
```

The runner needs three things:
1. A **`docker` CLI + the compose plugin** in the image (public base `docker:27-cli`
   provides both).
2. **`DOCKER_HOST`** pointed at a Docker socket proxy, so its `docker compose` talks to
   the host daemon (set via `state.home_env` or step `env`).
3. A proxy that **permits the Docker API groups compose uses** — containers, **networks,
   volumes**, images, and **exec** (for `--wait` health).

> **The proxy is already warm and shared — you don't start one per run.** BNK Forge runs
> a long-lived `tecnativa/docker-socket-proxy` (one instance, `restart: unless-stopped`,
> on `127.0.0.1:2375`) that *every* container module already routes through. Its default
> ACLs are deliberately narrow (`CONTAINERS=1, POST=1, IMAGES=1` but
> `EXEC=0, VOLUMES=0, NETWORKS=0`) — enough for a single `docker run`, **not** for
> compose. Widening that *shared* proxy enables networks/volumes/exec for **all** runners
> (a bigger blast radius on a root-equivalent surface). The clean alternative is a
> **dedicated, capability-scoped proxy** for compose runners — see §14.6.

### 14.2 Two ways to provide the compose file

**Pattern A — bake it in (recommended, per-app).** Build a small runner image that
`COPY`s the app's `docker-compose.yml` (+ any config) and exposes thin entrypoints. The
module is the app's versioned, digest-pinned deployer. Best when you have a specific app
to ship.

**Pattern B — generic runner.** One `compose-runner` image; the compose file is provided
at deploy time — pasted as a (large) user input or fetched from a git URL by the
entrypoint — written into the workspace, then `docker compose up`. Best for "deploy
*arbitrary* compose without rebuilding."

Both use the same artifact shape; only where the compose file comes from differs. We show
Pattern A.

### 14.3 The runner image (Pattern A, Grafana instance)

`docker-compose.yml` (a normal Grafana + Prometheus stack — nothing BNK-Forge-specific):

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.54.1
    command: ["--config.file=/etc/prometheus/prometheus.yml",
              "--storage.tsdb.retention.time=${RETENTION_DAYS:-15}d"]
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml:ro", "prom-data:/prometheus"]
    networks: [obs]
    mem_limit: 512m                       # bound the STACK (the runner's limits don't)
    cpus: 0.5
  grafana:
    image: grafana/grafana:11.2.0
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?set in .env}
    # Do NOT hard-pin a host port for multi-tenant use — two stacks both binding
    # 3000 collide. Pick ONE:
    #   (a) publish a dynamic host port and read it back into outputs:
    ports: ["3000"]                       # host side omitted → Docker assigns one
    #   (b) or publish nothing and reach Grafana by container DNS on a shared network
    #       (attach the EXTERNAL network below and drop `ports:` entirely).
    volumes: ["grafana-data:/var/lib/grafana", "./provisioning:/etc/grafana/provisioning:ro"]
    networks: [obs]
    depends_on: [prometheus]
    mem_limit: 512m
    cpus: 0.5
volumes: { prom-data: {}, grafana-data: {} }   # named volumes persist across re-applies
networks:
  obs: {}
  # To sit on a network BNK Forge services already share (reach Grafana by DNS, no
  # published port), attach an EXTERNAL one instead and put services on it:
  # default: { external: true, name: bnk-forge_default }
```

`compose-up` / `compose-down` (thin entrypoints baked into the image — a shell here is
fine; the argv-only rule applies to BNK Forge *steps*, not your image internals):

```sh
#!/bin/sh
# compose-up: materialize env, bring the stack up, write outputs.
set -eu
cd /app
P="${COMPOSE_PROJECT:-app}"                       # isolate per BNK Forge project
# Secrets/config arrive as env (injected by BNK Forge) → render the compose .env file.
: "${GRAFANA_ADMIN_PASSWORD:?missing}"
printf 'GRAFANA_ADMIN_PASSWORD=%s\nRETENTION_DAYS=%s\n' \
  "$GRAFANA_ADMIN_PASSWORD" "${RETENTION_DAYS:-15}" > .env

docker compose -p "$P" up -d --wait            # --wait blocks until healthy

# Resolve the dynamically-assigned host port (we did NOT hard-pin 3000) so the URL
# is correct and collision-free across concurrent deployments.
HOSTPORT=$(docker compose -p "$P" port grafana 3000 2>/dev/null | cut -d: -f2)

# Surface endpoints as the module's outputs.
OUT="${OUTPUTS_PATH:-/work/outputs.json}"; mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<EOF
{ "grafana_url": "http://$(hostname -i 2>/dev/null || echo localhost):${HOSTPORT:-3000}",
  "grafana_host_port": "${HOSTPORT:-}",
  "grafana_admin_user": "admin",
  "compose_project": "$P",
  "services": "grafana,prometheus" }
EOF
echo "stack up; outputs at $OUT"; cat "$OUT"
```

```sh
#!/bin/sh
# compose-down: tear the stack down. No-op-succeed if nothing is running.
set -eu
cd /app
P="${COMPOSE_PROJECT:-app}"
docker compose -p "$P" down --volumes --remove-orphans || true
echo "stack down (project $P)"
```

`Dockerfile`:

```dockerfile
FROM docker:27-cli                 # public image: docker CLI + compose plugin
WORKDIR /app
COPY docker-compose.yml prometheus.yml ./
COPY provisioning/ ./provisioning/
COPY compose-up compose-down /usr/local/bin/
RUN chmod +x /usr/local/bin/compose-up /usr/local/bin/compose-down
```

Build, push, capture the digest (as in §13).

### 14.4 The artifact manifest

`compose-grafana/bnkforge.artifact.json`:

```jsonc
{
  "schema_version": 1,
  "name": "compose-grafana",
  "version": "1.0.0",
  "kind": "container_image",
  "description": "Deploys a Grafana+Prometheus docker-compose stack beside BNK Forge.",
  "container_image": { "registry_host": "ghcr.io", "repository": "you/compose-grafana", "digest": "sha256:…" },
  "lifecycle": { "supports_apply": true, "supports_destroy": true },
  "state": {
    "mount_path": "/work",
    "outputs_file": "outputs.json",
    // Give the runner the daemon + a stable compose project name per deployment.
    "home_env": {
      "DOCKER_HOST": "tcp://docker-socket-proxy:2375",
      "OUTPUTS_PATH": "/work/outputs.json"
    }
  },
  "execution": { "engine": "container", "limits": { "cpus": "1", "memory": "512m" } },
  "steps": {
    "apply": [
      { "name": "compose-up",
        "args": ["compose-up"],                       // argv-only entrypoint; no shell
        "env": {
          "GRAFANA_ADMIN_PASSWORD": "{{inputs.grafana_admin_password}}",
          "RETENTION_DAYS": "{{inputs.retention_days}}",
          "COMPOSE_PROJECT": "{{inputs.project_slug}}" // unique per BNK Forge project
        },
        "timeout_seconds": 600,
        "retry": { "max_attempts": 2, "backoff_seconds": 30 } }
    ],
    "destroy": [
      { "name": "compose-down", "args": ["compose-down"],
        "env": { "COMPOSE_PROJECT": "{{inputs.project_slug}}" },
        "timeout_seconds": 300 }
    ]
  },
  "inputs": {
    "required": [
      { "name": "grafana_admin_password", "type": "string", "source": "user",
        "sensitive": true, "description": "Initial Grafana admin password." },
      { "name": "project_slug", "type": "string", "source": "project", "source_field": "name",
        "description": "Compose project name — isolates this stack from others on the host." }
    ],
    "optional": [
      { "name": "retention_days", "type": "number", "source": "user", "default": 15,
        "description": "Prometheus retention (days)." }
    ]
  },
  "references": []
}
```

A single module is enough — the **compose file already encodes the multi-service
topology and dependency order** (`depends_on`), so you don't split it across BNK Forge
modules. The blueprint is then a one-module wrapper (like §13.3) exposing
`grafana_admin_password` + `retention_days` on the deploy form.

### 14.5 What you get, and the rules that matter

- **`apply` = `docker compose up -d --wait`.** Idempotent by nature — re-applying
  reconciles the running stack. **`destroy` = `docker compose down`**, no-op-succeeding
  when nothing is up.
- **Persistence:** named volumes in the compose file (`grafana-data`, `prom-data`)
  survive re-applies and worker restarts — that's where the app's state lives, not the
  BNK Forge workspace.
- **Isolation:** pass a unique `-p <project_slug>` (sourced from the BNK Forge project
  name) so multiple deployments don't collide on container/volume/network names — and so
  `down --remove-orphans` only ever touches *this* stack (see §14.7).
- **Ports:** don't hard-pin host ports for multi-tenant use — two stacks both binding
  `3000` collide. Publish a **dynamic** port (`ports: ["3000"]`) and read it back into
  outputs, or publish nothing and reach the service by **container DNS on a shared
  network**.
- **Resource bounds:** `execution.limits` bound the **runner**, not the containers it
  launches. Put `mem_limit`/`cpus` (or `deploy.resources`) on the compose **services** to
  bound the stack on the host.
- **"Same environment":** the stack runs on the BNK Forge Docker host. To put it on a
  network BNK Forge services already share, attach an **external** network in the compose
  file (and reach it by DNS) instead of publishing host ports.
- **Secrets:** the admin password is a **sensitive input**, injected as env and rendered
  into a transient `.env` inside the workspace — never in the manifest, masked in logs.
- **Outputs:** the runner writes `outputs.json` (the Grafana URL + resolved host port,
  admin user, compose project) → surfaced as module outputs and wireable into downstream
  modules.

### 14.6 The prerequisite — a capability-scoped Docker socket proxy

`docker compose` needs more of the Docker API than a single `docker run`: it creates
**networks** and **volumes**, pulls **images**, and uses **exec** for `--wait` health
checks. BNK Forge's default proxy is deliberately narrow
(`CONTAINERS=1, POST=1, IMAGES=1`; `EXEC=0, VOLUMES=0, NETWORKS=0`) — enough for the
engine's own `docker run`, not for compose.

You have two ways to grant the extra access; **prefer the second**:

**A. Widen the shared proxy (simplest, broadest blast radius).** Flip `NETWORKS=1`,
`VOLUMES=1`, `EXEC=1` on the existing `docker-socket-proxy` service. *Every* container
module now gets those rights — you've widened a root-equivalent surface for runners that
don't need it. Acceptable only if you trust all your module images.

**B. Add a dedicated, capability-scoped proxy (recommended).** Stand up a *second*
long-lived proxy with the wider ACLs on its own port, and point **only compose-capable
modules** at it. The narrow proxy stays the default for everything else.

```yaml
# docker-compose.local.yml (or your overlay) — alongside the existing proxy
  docker-socket-proxy-compose:
    image: tecnativa/docker-socket-proxy:0.3.0
    container_name: bnk-forge-docker-socket-proxy-compose
    ports: ["127.0.0.1:2376:2375"]      # different host port than the default proxy
    environment:
      CONTAINERS: "1"
      POST: "1"
      IMAGES: "1"
      NETWORKS: "1"                       # compose creates networks
      VOLUMES: "1"                        # compose creates volumes
      EXEC: "1"                           # compose --wait health checks
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    restart: unless-stopped
```

Then a compose module's manifest points `DOCKER_HOST` at the wider proxy
(`tcp://127.0.0.1:2376` for host-net workers, or the service name on a bridge network),
while the roksbnkctl/Terraform/scan runners keep using the default `:2375`. The wider
scope is isolated to the modules that actually need it.

> The proxy is **warm and shared either way** — you reuse a standing service, you don't
> start one per run. Choice B just gives you a *second* standing service so the broad
> rights aren't handed to every runner.

### 14.7 Contention between runners

All runners share the host's **single Docker daemon** (the proxy only gates the API).
BNK Forge's `module_lock` already prevents the *same* module from running twice, but it
does **not** stop different modules/projects from contending on shared Docker resources.
Manage that by **namespacing**, not isolation:

| Contention | Mitigation |
|---|---|
| Container / network / volume **name collisions** | `docker compose -p <project_slug>` namespaces every resource per deployment. Source `project_slug` from the BNK Forge project (a `source: project` input). |
| One teardown nuking another stack | `down --remove-orphans` is scoped to `-p`, so unique project names make it safe. **Never share a project name** across deployments. |
| **Host port collisions** | Don't hard-pin host ports. Publish dynamic ports and read them into outputs, or reach services by DNS on a shared network with no published port. |
| **Host resource exhaustion** | The runner's `execution.limits` don't bound what it launches — set `mem_limit`/`cpus`/`deploy.resources` on the compose services. |
| Daemon / proxy throughput | haproxy + the daemon handle concurrent API calls fine; once names + ports are namespaced, concurrency is a resource concern, not a correctness one. |

If you need **true isolation** between runners (not just namespacing), the heavy option
is Docker-in-Docker — each runner gets its own daemon. That loses the "same environment
as BNK Forge" benefit and costs a lot more, so reserve it for genuinely hostile or
resource-sensitive multi-tenancy. For deploying *beside* BNK Forge, a shared daemon with
per-project namespacing is the right model.

> **Takeaway:** any app with a `docker-compose.yml` becomes a BNK Forge module by
> wrapping `compose up`/`compose down` in argv entrypoints, pointing the runner's
> `DOCKER_HOST` at a **capability-scoped** proxy, **namespacing per project** (`-p`,
> dynamic ports, bounded resources), and surfacing endpoints as outputs. Grafana is just
> one instance — swap the compose file for any stack.

---

## 15. Field reference (cheat sheet)

**Artifact manifest top-level**

| Key | Required | Notes |
|---|---|---|
| `schema_version` | ✓ | `1` |
| `name`, `version` | ✓ | identity |
| `kind` | ✓ | `container_image` |
| `container_image` | ✓ | `{ registry_host, repository, digest }` — digest-pinned |
| `lifecycle` | ✓ | `{ supports_apply, supports_destroy, … }` |
| `state` | ✓ | workspace: `{ mount_path, outputs_file, home_env, scope }` |
| `execution` | ✓ | `{ engine: "container", limits: { cpus, memory, pids } }` |
| `steps` | ✓ | `{ init?, plan?, apply, destroy? }` step-sets |
| `inputs` | – | `{ required: [...], optional: [...] }` |
| `credentials` | – | `{ cloud: "ibmcloud" }` injects creds as env |
| `cluster` | – | cluster-registration contract (§8) |
| `references` | – | artifact references |

**Step**

| Key | Notes |
|---|---|
| `name` | log label |
| `args` | argv vector in *this* image; first token not a shell |
| `timeout_seconds` | hard per-step cap (exit 124, not retried) |
| `when` | `{{…}}` gate; falsy → skip |
| `env` | extra env (templated) |
| `run_once` | skip on re-apply after first success |
| `retry` | `{ max_attempts, backoff_seconds }` (soft failures only) |

**`cluster` block**

| Key | Notes |
|---|---|
| `name_output` | output key holding the cluster name |
| `api_server_output` | output key holding the API server URL |
| `region_output` | output key holding the region |
| `cloud_provider` | drives credential-template refresh (`ibm`/`aws`/…; case-insensitive) |
| `kubeconfig_file` | workspace-relative kubeconfig path, **or** |
| `kubeconfig_output` | output key holding the kubeconfig (YAML or base64) |

---

## 16. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Step rejected at sync ("must not contain 'command'/'shell'") | You used a shell or command-string. Bake logic into the image; use argv. |
| "Floating tag" / digest error | `container_image.digest` must be a pinned `sha256:…`, not a tag. |
| Re-apply fails because setup "already exists" | Mark the setup step `run_once: true`. |
| A failed step retries forever-ish then gives up | `retry` is retrying a *deterministic* failure. Fail fast in the container, or remove retry for that step. |
| Tool writes to `$HOME` and gets "permission denied" | Redirect its HOME/cache into `mount_path` via `state.home_env`. |
| Outputs come back `{}` | The artifact didn't write `state.outputs_file`, or wrote it to the wrong path (must be relative to `mount_path`). |
| Cluster not registered | No `cluster` block, or the declared `kubeconfig_file`/`kubeconfig_output` wasn't produced. Check the module actually wrote the kubeconfig to the declared workspace path. |
| Cluster registered but session expires | You surfaced an admin-cert kubeconfig (not refreshed) — surface a **token-based** kubeconfig and set `cloud_provider` so BNK Forge re-mints the token from the credential template. |
| Kubeconfig builder fails with "no certificate-authority-data" | For public-cert API servers (IBM ROKS) that's normal — omit the CA and rely on system trust; don't require it. |
| Next module in the blueprint never starts | Dependency not satisfied — confirm `depends_on` and that the upstream module reached `applied` and produced the wired output. |
| Destroy stalls on a no-op phase | A `*-down` step errored on empty state — make destroy no-op-succeed when there's nothing to tear down. |
| `docker compose` runner gets `403`/permission denied from the daemon | The Docker socket proxy isn't scoped for compose. Point the runner's `DOCKER_HOST` at the proxy **and** enable the API groups compose needs (containers, networks, volumes, images, exec). See §14.6. |
| Compose stack's data is gone after re-apply | App state must live in **named volumes** in the compose file, not the BNK Forge workspace. |
| Second deployment collides with the first | Pass a unique `-p <project_slug>` (compose project name) per BNK Forge project. |

---

*Authoring container runner modules is mostly a matter of three things: a digest-pinned
image that does the work via its entrypoint, an artifact manifest that declares the
steps + workspace + outputs (+ optional cluster contract), and a blueprint that wires
modules together by output. Start from Example 1, then compose like Example 2.*
