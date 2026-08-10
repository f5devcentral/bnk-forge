# Multi-Platform Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make BNK Forge fully platform-aware so that deployments work on EKS, AKS, GKE, OCP (OpenShift), and Ubuntu/on-prem (kind/microk8s) — not just AWS/EKS.

**Architecture:** Two-repo change. The modules repo (`bnk-forge-modules`, branch `release/2.2`) contains Terraform modules that currently hardcode `data.aws_eks_cluster` for kubeconfig generation — these must become platform-agnostic by consuming a Forge-injected kubeconfig variable. The Forge repo (`bnk-forge-v2`, branch `agent/platform-awareness`) already has the platform context foundation (enums, detection, capabilities) but needs: (1) the config_writer to inject a universal kubeconfig variable, (2) new stack templates per platform, (3) platform-specific cluster registration, and (4) frontend follow-through.

**Tech Stack:** Terraform/OpenTofu (HCL), Python/FastAPI, React/TypeScript, PostgreSQL (Alembic migrations)

**Repos:**
- `bnk-forge-modules` @ `release/2.2` — Terraform modules
- `bnk-forge-v2` @ `agent/platform-awareness` — Forge platform

---

## Problem Summary

### Current State
6 modules in `bnk-forge-modules` hardcode `data.aws_eks_cluster.cluster` for kubeconfig:
- `bnk/flo/main.tf`
- `bnk/cneinstance/main.tf`
- `bnk/bnk-vlans/main.tf`
- `bnk/bnk-gatewayclass/main.tf`
- `k8s/bnk-prerequisites/main.tf`
- `k8s/cert-manager/main.tf`

Forge's `config_writer.py` already has `generate_aws_eks_providers()`, `generate_azure_aks_providers()`, `generate_gcp_gke_providers()`, and `generate_generic_providers()`. But it injects `data.aws_eks_cluster` data sources for AWS and `config_path` for generic — and the modules only understand the AWS pattern.

### Root Cause
Each module builds its own kubeconfig from `data.aws_eks_cluster.cluster.endpoint` + `data.aws_eks_cluster_auth.cluster.token`. This couples every module to AWS EKS. The fix is to make modules consume a **platform-agnostic kubeconfig** that Forge provides regardless of platform.

### Design Decision: Universal Kubeconfig Injection

Rather than duplicating modules per platform (`bnk/aws/flo`, `bnk/ocp/flo`, etc.), we:

1. **Modules** accept a `forge_kubeconfig_content` variable (YAML string) and write it to disk
2. **Forge's config_writer** generates kubeconfig content for ANY platform and injects it as a Terraform variable
3. **Stack templates** carry per-platform variable defaults (storage_class, container_platform, etc.)
4. **Infra modules** remain platform-specific by nature (`infra/aws/`, `infra/ocp/`, `infra/ubuntu/`, `infra/azure/`)

This avoids module duplication while keeping platform-specific behavior in variables and templates.

---

## Phase 1: Modules Repo — Platform-Agnostic Kubeconfig (bnk-forge-modules @ release/2.2)

### Task 1: Add universal kubeconfig variable to shared module pattern

All 6 affected modules follow the same pattern: they declare a `local_file.kubeconfig` resource that reads from `data.aws_eks_cluster`. We replace this with a variable-driven approach.

**Files (bnk-forge-modules repo):**
- Create: `shared/kubeconfig.tf` (reference template, not a real Terraform module — just documentation)
- Modify: `bnk/flo/variables.tf`
- Modify: `bnk/flo/main.tf`

- [ ] **Step 1: Add `forge_kubeconfig_content` variable to `bnk/flo/variables.tf`**

Add at the top of the CLUSTER / NAMESPACE section:

```hcl
variable "forge_kubeconfig_content" {
  description = "Kubeconfig YAML content injected by BNK-Forge. Platform-agnostic — works for EKS, AKS, GKE, OCP, or generic clusters."
  type        = string
  default     = ""
  sensitive   = true
}
```

- [ ] **Step 2: Replace hardcoded AWS kubeconfig in `bnk/flo/main.tf`**

Replace the existing `local_file.kubeconfig` resource and remove the `data.aws_eks_cluster` references.

Old pattern (remove):
```hcl
resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  content = yamlencode({
    apiVersion = "v1"
    kind       = "Config"
    clusters = [{
      name = "cluster"
      cluster = {
        server                     = data.aws_eks_cluster.cluster.endpoint
        certificate-authority-data = data.aws_eks_cluster.cluster.certificate_authority[0].data
      }
    }]
    users = [{
      name = "user"
      user = {
        token = data.aws_eks_cluster_auth.cluster.token
      }
    }]
    contexts = [{
      name = "default"
      context = {
        cluster = "cluster"
        user    = "user"
      }
    }]
    current-context = "default"
  })
}
```

New pattern (add):
```hcl
resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  content         = var.forge_kubeconfig_content
}
```

- [ ] **Step 3: Remove `aws` from `bnk/flo/versions.tf` required_providers**

Remove the `aws` provider requirement since the module no longer uses AWS data sources:

```hcl
# Remove this block from required_providers:
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0.0"
    }
```

Keep `kubernetes`, `helm`, `local`, `time`, `null`.

- [ ] **Step 4: Verify no remaining `data.aws_` references in `bnk/flo/`**

Run: `grep -r "data.aws_" bnk/flo/`
Expected: No matches

- [ ] **Step 5: Commit**

```bash
git add bnk/flo/
git commit -m "refactor(bnk/flo): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 2: Apply same pattern to bnk/cneinstance

**Files:**
- Modify: `bnk/cneinstance/variables.tf`
- Modify: `bnk/cneinstance/main.tf`
- Modify: `bnk/cneinstance/versions.tf`

- [ ] **Step 1: Add `forge_kubeconfig_content` variable to `bnk/cneinstance/variables.tf`**

```hcl
variable "forge_kubeconfig_content" {
  description = "Kubeconfig YAML content injected by BNK-Forge. Platform-agnostic."
  type        = string
  default     = ""
  sensitive   = true
}
```

- [ ] **Step 2: Replace kubeconfig resource in `bnk/cneinstance/main.tf`**

Same replacement as Task 1 Step 2 — replace `local_file.kubeconfig` content with `var.forge_kubeconfig_content`.

- [ ] **Step 3: Remove `aws` provider from `bnk/cneinstance/versions.tf`**

- [ ] **Step 4: Verify**: `grep -r "data.aws_" bnk/cneinstance/` — no matches

- [ ] **Step 5: Commit**

```bash
git add bnk/cneinstance/
git commit -m "refactor(bnk/cneinstance): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 3: Apply same pattern to bnk/bnk-vlans

**Files:**
- Modify: `bnk/bnk-vlans/variables.tf`
- Modify: `bnk/bnk-vlans/main.tf`
- Modify: `bnk/bnk-vlans/versions.tf`

Same steps as Task 2. Add variable, replace kubeconfig resource, remove aws provider, verify, commit.

```bash
git commit -m "refactor(bnk/bnk-vlans): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 4: Apply same pattern to bnk/bnk-gatewayclass

**Files:**
- Modify: `bnk/bnk-gatewayclass/variables.tf`
- Modify: `bnk/bnk-gatewayclass/main.tf`
- Modify: `bnk/bnk-gatewayclass/versions.tf`

Same steps. Commit:

```bash
git commit -m "refactor(bnk/bnk-gatewayclass): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 5: Apply same pattern to k8s/bnk-prerequisites

**Files:**
- Modify: `k8s/bnk-prerequisites/variables.tf`
- Modify: `k8s/bnk-prerequisites/main.tf`
- Modify: `k8s/bnk-prerequisites/versions.tf`

Same steps. Commit:

```bash
git commit -m "refactor(k8s/bnk-prerequisites): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 6: Apply same pattern to k8s/cert-manager

**Files:**
- Modify: `k8s/cert-manager/variables.tf`
- Modify: `k8s/cert-manager/main.tf`
- Modify: `k8s/cert-manager/versions.tf`

Same steps. Commit:

```bash
git commit -m "refactor(k8s/cert-manager): replace hardcoded AWS kubeconfig with forge_kubeconfig_content variable"
```

---

### Task 7: Validate all modules are AWS-free

- [ ] **Step 1: Search entire repo for remaining AWS data source references**

```bash
grep -r "data.aws_eks_cluster" bnk/ k8s/
```
Expected: No matches (only `infra/aws/` should have AWS references)

- [ ] **Step 2: Search for aws provider requirements outside infra/**

```bash
grep -rl "hashicorp/aws" bnk/ k8s/ app/
```
Expected: No matches

- [ ] **Step 3: Run `terraform validate` on each modified module** (dry-run, will fail without variables but should parse)

```bash
for dir in bnk/flo bnk/cneinstance bnk/bnk-vlans bnk/bnk-gatewayclass k8s/bnk-prerequisites k8s/cert-manager; do
  echo "=== $dir ==="
  cd "$dir" && terraform init -backend=false 2>&1 | tail -3 && cd -
done
```

- [ ] **Step 4: Commit validation results (if any fixes needed)**

---

## Phase 2: Forge Backend — Kubeconfig Injection (bnk-forge-v2 @ agent/platform-awareness)

### Task 8: Update config_writer to inject forge_kubeconfig_content variable

The `write_provider_config()` function in `backend/services/execution/config_writer.py` currently generates `bnk_forge_providers.tf` with data sources. It needs to also generate a `forge_kubeconfig_content` value in `terraform.tfvars.json`.

**Files:**
- Modify: `backend/services/execution/config_writer.py:270-415` (the `write_provider_config` function)
- Modify: `backend/utils/provider_config.py` — add kubeconfig content generation functions

- [ ] **Step 1: Add kubeconfig content generators to `backend/utils/provider_config.py`**

Add these functions:

```python
def generate_kubeconfig_content_aws(cluster_name: str, aws_region: str, endpoint: str, ca_data: str, token: str) -> str:
    """Generate kubeconfig YAML content for AWS EKS."""
    import yaml
    return yaml.dump({
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "cluster", "cluster": {
            "server": endpoint,
            "certificate-authority-data": ca_data,
        }}],
        "users": [{"name": "user", "user": {"token": token}}],
        "contexts": [{"name": "default", "context": {"cluster": "cluster", "user": "user"}}],
        "current-context": "default",
    })


def generate_kubeconfig_content_from_file(kubeconfig_path: str, context: str | None = None) -> str:
    """Read kubeconfig from file for generic/on-prem clusters."""
    with open(kubeconfig_path) as f:
        return f.read()
```

**NOTE:** For AWS, the kubeconfig with a static token is short-lived (15 min). The existing `bnk_forge_providers.tf` pattern using `data.aws_eks_cluster_auth` refreshes the token on each plan/apply. We need to keep the data source injection approach for AWS but ALSO provide the kubeconfig variable for kubectl usage.

**Revised approach:** Instead of replacing provider injection, ADD kubeconfig content as an additional variable. For AWS, the injected `bnk_forge_providers.tf` still creates the data sources, AND we generate a kubeconfig tfvar from those data sources using a Terraform local:

- [ ] **Step 2: Update `write_provider_config` to emit a kubeconfig local**

In `config_writer.py`, after writing `bnk_forge_providers.tf`, append a kubeconfig local that the modules can reference:

For AWS (append to the generated `bnk_forge_providers.tf`):
```hcl
# Platform-agnostic kubeconfig for kubectl local-exec
locals {
  _forge_kubeconfig_yaml = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    clusters        = [{ name = "cluster", cluster = {
      server                     = data.aws_eks_cluster.cluster.endpoint
      certificate-authority-data = data.aws_eks_cluster.cluster.certificate_authority[0].data
    }}]
    users           = [{ name = "user", user = { token = data.aws_eks_cluster_auth.cluster.token }}]
    contexts        = [{ name = "default", context = { cluster = "cluster", user = "user" }}]
    current-context = "default"
  })
}
```

For Azure (uses `azurerm_kubernetes_cluster` data source):
```hcl
locals {
  _forge_kubeconfig_yaml = data.azurerm_kubernetes_cluster.cluster.kube_config_raw
}
```

For GCP:
```hcl
locals {
  _forge_kubeconfig_yaml = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    clusters        = [{ name = "cluster", cluster = {
      server                     = "https://${data.google_container_cluster.cluster.endpoint}"
      certificate-authority-data = data.google_container_cluster.cluster.master_auth[0].cluster_ca_certificate
    }}]
    users           = [{ name = "user", user = { token = data.google_client_config.default.access_token }}]
    contexts        = [{ name = "default", context = { cluster = "cluster", user = "user" }}]
    current-context = "default"
  })
}
```

For generic/on-prem:
```hcl
locals {
  _forge_kubeconfig_yaml = file("${var.kubeconfig_path}")
}
```

- [ ] **Step 3: Write forge_kubeconfig_content into tfvars**

In the `write_tfvars` flow (or as a Terraform variable default in `bnk_forge_providers.tf`), add:

```hcl
variable "forge_kubeconfig_content" {
  description = "Injected by BNK-Forge — do not set manually"
  type        = string
  default     = ""
  sensitive   = true
}
```

And append to `bnk_forge_providers.tf`:
```hcl
# Wire the platform-specific kubeconfig into the module variable
locals {
  # Modules reference var.forge_kubeconfig_content which is set here
}

# Override the module's default with the platform-generated kubeconfig
# This is done via a terraform.tfvars.json entry OR via the local above
```

**IMPORTANT DESIGN NOTE:** Because the kubeconfig token for AWS is dynamic (from `data.aws_eks_cluster_auth`), we can't put it in `terraform.tfvars.json` (that's static). Instead, add to `bnk_forge_providers.tf`:

```hcl
variable "forge_kubeconfig_content" {
  type      = string
  default   = ""
  sensitive = true
}

locals {
  forge_kubeconfig = var.forge_kubeconfig_content != "" ? var.forge_kubeconfig_content : local._forge_kubeconfig_yaml
}
```

Then update the module variable in each module to use `local.forge_kubeconfig` ... but wait, modules don't see parent locals. Since these are standalone modules (not called as child modules), the `bnk_forge_providers.tf` is written INTO the module workspace directory. So the local IS available inside the module.

**Final approach for modules (revise Task 1-6):**

The modules should reference `local.forge_kubeconfig` instead of `var.forge_kubeconfig_content`:

```hcl
resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  content         = local.forge_kubeconfig
}
```

And the `forge_kubeconfig_content` variable + `local.forge_kubeconfig` are defined in the Forge-injected `bnk_forge_providers.tf`, not in the module itself. This means:

- Modules DON'T need a `forge_kubeconfig_content` variable (revise Tasks 1-6)
- Modules just reference `local.forge_kubeconfig` which Forge injects
- Forge generates the right kubeconfig local per platform

- [ ] **Step 4: Commit**

```bash
git add backend/services/execution/config_writer.py backend/utils/provider_config.py
git commit -m "feat: inject platform-agnostic kubeconfig local into bnk_forge_providers.tf"
```

---

### Task 8b (REVISION): Update modules to use local.forge_kubeconfig instead of variable

Go back to Tasks 1-6 in the modules repo. Instead of adding a `forge_kubeconfig_content` variable, change the `local_file.kubeconfig` to:

```hcl
resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  content         = local.forge_kubeconfig
}
```

Remove `data.aws_eks_cluster` references and `aws` provider requirement. The `local.forge_kubeconfig` is provided by Forge's injected `bnk_forge_providers.tf`.

**IMPORTANT:** Keep a `variable "forge_kubeconfig_content"` in the module as a fallback for standalone usage (outside of Forge), with a default of `""`. Add a `locals` block:

```hcl
locals {
  # forge_kubeconfig is injected by BNK-Forge via bnk_forge_providers.tf
  # When running standalone (outside Forge), set forge_kubeconfig_content variable instead
  _module_kubeconfig = var.forge_kubeconfig_content
}
```

Then Forge's `bnk_forge_providers.tf` overrides with:
```hcl
locals {
  forge_kubeconfig = <platform-specific-kubeconfig>
}
```

And modules use `local.forge_kubeconfig` (injected by Forge) falling back to `var.forge_kubeconfig_content` (standalone).

**FINAL MODULE PATTERN:**

In each module's `variables.tf`:
```hcl
variable "forge_kubeconfig_content" {
  description = "Kubeconfig YAML. Automatically injected by BNK-Forge. Set manually for standalone usage."
  type        = string
  default     = ""
  sensitive   = true
}
```

In each module's `main.tf`:
```hcl
resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  # local.forge_kubeconfig is injected by BNK-Forge's bnk_forge_providers.tf
  # Falls back to var.forge_kubeconfig_content for standalone use
  content = try(local.forge_kubeconfig, var.forge_kubeconfig_content)
}
```

The `try()` function makes this work both inside Forge (where `local.forge_kubeconfig` exists) and standalone (where only the variable exists).

---

### Task 9: Add tests for config_writer platform-specific kubeconfig injection

**Files:**
- Modify: `backend/tests/component/test_config_writer.py`

- [ ] **Step 1: Write test for AWS kubeconfig local generation**

```python
def test_write_provider_config_aws_emits_kubeconfig_local(tmp_path, mock_module):
    """AWS provider injection should include forge_kubeconfig local."""
    mock_module.project.cloud_provider = "aws"
    write_provider_config(str(tmp_path), mock_module, variables={"cluster_name": "test-cluster", "region": "us-east-1"})
    content = (tmp_path / "bnk_forge_providers.tf").read_text()
    assert "local.forge_kubeconfig" in content or "forge_kubeconfig" in content
    assert "data.aws_eks_cluster" in content  # Still uses data source for dynamic token
```

- [ ] **Step 2: Write test for generic kubeconfig local generation**

```python
def test_write_provider_config_generic_emits_kubeconfig_local(tmp_path, mock_module):
    """Generic provider injection should include forge_kubeconfig from file."""
    mock_module.project.cloud_provider = ""
    write_provider_config(str(tmp_path), mock_module, variables={"cluster_name": "test", "kubeconfig_path": "/app/kubeconfig"})
    content = (tmp_path / "bnk_forge_providers.tf").read_text()
    assert "forge_kubeconfig" in content
    assert "data.aws_eks_cluster" not in content
```

- [ ] **Step 3: Write test for Azure kubeconfig local generation**

```python
def test_write_provider_config_azure_emits_kubeconfig_local(tmp_path, mock_module):
    """Azure provider injection should include forge_kubeconfig from AKS."""
    mock_module.project.cloud_provider = "azure"
    write_provider_config(str(tmp_path), mock_module, variables={"cluster_name": "test", "resource_group_name": "rg-test"})
    content = (tmp_path / "bnk_forge_providers.tf").read_text()
    assert "forge_kubeconfig" in content
    assert "azurerm_kubernetes_cluster" in content
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/component/test_config_writer.py -v -k "kubeconfig_local"
```

- [ ] **Step 5: Commit**

```bash
git commit -m "test: add config_writer kubeconfig injection tests for all platforms"
```

---

## Phase 3: Stack Templates — Per-Platform Blueprints (bnk-forge-v2)

### Task 10: Add platform_defaults to stack template schema

**Files:**
- Modify: `backend/models/stack.py` — add `platform_defaults` JSON column to StackTemplate
- Create: `backend/alembic/versions/v2_054_add_platform_defaults_to_stack_templates.py`

- [ ] **Step 1: Add column to StackTemplate model**

In `backend/models/stack.py`, add to the `StackTemplate` class:

```python
platform_defaults = Column(JSON, nullable=True, default=None, comment="Per-platform variable overrides: {platform_profile: {var: value}}")
```

- [ ] **Step 2: Generate Alembic migration**

```bash
cd backend && alembic revision --autogenerate -m "add platform_defaults to stack_templates"
```

Review the generated migration, ensure it only adds the column.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add platform_defaults column to StackTemplate model"
```

---

### Task 11: Update stack_templates.json with platform defaults

**Files:**
- Modify: `backend/data/stack_templates.json`

- [ ] **Step 1: Add platform_defaults to the "f5-bnk-2.2" template**

```json
{
  "slug": "f5-bnk-2.2",
  "cloud_provider": "any",
  "platform_defaults": {
    "eks": {
      "container_platform": "AWS",
      "storage_class_name": "gp3",
      "cloud_provider": "aws"
    },
    "aks": {
      "container_platform": "Azure",
      "storage_class_name": "managed-csi",
      "cloud_provider": "azure"
    },
    "gke": {
      "container_platform": "Generic",
      "storage_class_name": "standard-rwo",
      "cloud_provider": "gcp"
    },
    "ocp": {
      "container_platform": "Generic",
      "storage_class_name": "ocs-storagecluster-ceph-rbd",
      "cloud_provider": ""
    },
    "generic_onprem": {
      "container_platform": "Generic",
      "storage_class_name": "local-path",
      "cloud_provider": ""
    }
  }
}
```

Change `cloud_provider` from `"aws"` to `"any"` since it now supports all platforms.

- [ ] **Step 2: Update "bnk-on-k8s" template similarly**

Already `"any"` but add explicit platform_defaults.

- [ ] **Step 3: Keep "aws-k8s-foundation" as AWS-only** (this IS infra provisioning, inherently AWS)

- [ ] **Step 4: Add new "bnk-demo-apps" variants or make it platform-aware**

The demo apps blueprint currently hardcodes Bedrock. Add platform_defaults with alternative AI backends:

```json
"platform_defaults": {
  "eks": { "ai_backend": "bedrock" },
  "aks": { "ai_backend": "azure_openai" },
  "gke": { "ai_backend": "vertex_ai" },
  "generic_onprem": { "ai_backend": "ollama" }
}
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add platform_defaults to stack templates, make BNK blueprint platform-agnostic"
```

---

### Task 12: Wire platform_defaults into stack deployment

**Files:**
- Modify: `backend/services/stack_service.py` — apply platform_defaults when creating stack instances
- Modify: `backend/services/stack_deployment_service.py` — pass platform context to variable assembly

- [ ] **Step 1: Update `StackService.create_stack_instance` to merge platform defaults**

When a stack instance is created from a template, look up `project.cloud_provider`, resolve the `PlatformProfile`, and merge the matching `platform_defaults` into each module's variables:

```python
from core.platform_context import PlatformProfile
from services.platform_context_service import PlatformContextService

def _resolve_platform_profile(self, project) -> str:
    """Resolve platform profile from project settings."""
    svc = PlatformContextService()
    return svc.normalize_platform_profile(project.cloud_provider or "")

def _apply_platform_defaults(self, template, platform_profile: str, module_variables: dict) -> dict:
    """Merge platform-specific defaults into module variables."""
    if not template.platform_defaults:
        return module_variables
    defaults = template.platform_defaults.get(platform_profile, {})
    # Platform defaults are base; explicit module variables override
    merged = {**defaults, **module_variables}
    return merged
```

- [ ] **Step 2: Apply in create_stack_instance flow**

In the method that creates `ProjectModule` rows from a template, call `_apply_platform_defaults` for each module's variables before saving.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: wire platform_defaults into stack instance creation"
```

---

## Phase 4: New Infrastructure Templates (bnk-forge-modules @ release/2.2)

### Task 13: Create infra/ubuntu module (kind cluster provisioning)

**Files (bnk-forge-modules):**
- Create: `infra/ubuntu/kind/main.tf`
- Create: `infra/ubuntu/kind/variables.tf`
- Create: `infra/ubuntu/kind/outputs.tf`
- Create: `infra/ubuntu/kind/versions.tf`
- Create: `infra/ubuntu/kind/module.json`

This module provisions a kind cluster on an Ubuntu host using Ansible (via null_resource + local-exec) or the Forge AnsibleEngine.

- [ ] **Step 1: Create module.json metadata**

```json
{
  "name": "Ubuntu Kind Cluster",
  "description": "Provisions a kind Kubernetes cluster on Ubuntu for BNK development/testing",
  "category": "infra",
  "platform": "ubuntu",
  "engine": "opentofu",
  "inputs": {
    "providers": {
      "required": ["null", "local"]
    }
  },
  "outputs": ["kubeconfig_content", "cluster_name", "cluster_endpoint"]
}
```

- [ ] **Step 2: Create variables.tf**

```hcl
variable "cluster_name" {
  description = "Name of the kind cluster"
  type        = string
  default     = "bnk-dev"
}

variable "kubernetes_version" {
  description = "Kubernetes version for kind node image"
  type        = string
  default     = "1.29.2"
}

variable "worker_nodes" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "ssh_host" {
  description = "SSH host for remote Ubuntu machine (empty = local)"
  type        = string
  default     = ""
}

variable "ssh_user" {
  description = "SSH user"
  type        = string
  default     = "ubuntu"
}

variable "ssh_private_key_path" {
  description = "Path to SSH private key"
  type        = string
  default     = ""
}
```

- [ ] **Step 3: Create main.tf**

```hcl
# infra/ubuntu/kind/main.tf
# Provisions a kind cluster on Ubuntu for BNK development/testing

resource "null_resource" "kind_cluster" {
  triggers = {
    cluster_name       = var.cluster_name
    kubernetes_version = var.kubernetes_version
    worker_nodes       = var.worker_nodes
  }

  provisioner "local-exec" {
    command = <<-EOT
      kind create cluster \
        --name ${var.cluster_name} \
        --image kindest/node:v${var.kubernetes_version} \
        --config ${path.module}/kind-config.yaml \
        --kubeconfig ${path.module}/work/kubeconfig
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "kind delete cluster --name ${self.triggers.cluster_name} || true"
  }
}

# Generate kind config with the requested number of workers
resource "local_file" "kind_config" {
  filename = "${path.module}/kind-config.yaml"
  content = yamlencode({
    kind       = "Cluster"
    apiVersion = "kind.x-k8s.io/v1alpha4"
    nodes = concat(
      [{ role = "control-plane" }],
      [for i in range(var.worker_nodes) : { role = "worker" }]
    )
  })
}

resource "local_file" "kubeconfig" {
  depends_on = [null_resource.kind_cluster]
  filename   = "${path.module}/work/kubeconfig"
  # kind creates this file; we reference it for outputs
  content    = file("${path.module}/work/kubeconfig")
}
```

- [ ] **Step 4: Create outputs.tf**

```hcl
output "kubeconfig_content" {
  description = "Kubeconfig for the kind cluster"
  value       = local_file.kubeconfig.content
  sensitive   = true
}

output "cluster_name" {
  value = var.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = "https://127.0.0.1:6443"  # kind default
}
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add infra/ubuntu/kind module for local development clusters"
```

---

### Task 14: Create infra/ocp module (OpenShift cluster connection)

OCP clusters are typically pre-provisioned. This module validates connectivity and outputs kubeconfig.

**Files (bnk-forge-modules):**
- Create: `infra/ocp/connect/main.tf`
- Create: `infra/ocp/connect/variables.tf`
- Create: `infra/ocp/connect/outputs.tf`
- Create: `infra/ocp/connect/versions.tf`
- Create: `infra/ocp/connect/module.json`

- [ ] **Step 1: Create module.json**

```json
{
  "name": "OpenShift Cluster Connection",
  "description": "Validates and connects to an existing OpenShift cluster",
  "category": "infra",
  "platform": "ocp",
  "engine": "opentofu",
  "inputs": {
    "providers": {
      "required": ["null", "local"]
    }
  },
  "outputs": ["kubeconfig_content", "cluster_name", "cluster_endpoint", "ocp_version"]
}
```

- [ ] **Step 2: Create variables.tf**

```hcl
variable "cluster_name" {
  description = "Name identifier for this OCP cluster"
  type        = string
}

variable "api_server_url" {
  description = "OpenShift API server URL (e.g., https://api.cluster.example.com:6443)"
  type        = string
}

variable "kubeconfig_content" {
  description = "Kubeconfig YAML content for the OCP cluster"
  type        = string
  sensitive   = true
  default     = ""
}

variable "oc_token" {
  description = "OpenShift token (alternative to kubeconfig)"
  type        = string
  sensitive   = true
  default     = ""
}
```

- [ ] **Step 3: Create main.tf**

```hcl
# infra/ocp/connect/main.tf
# Validates connectivity to an existing OpenShift cluster

locals {
  kubeconfig = var.kubeconfig_content != "" ? var.kubeconfig_content : yamlencode({
    apiVersion = "v1"
    kind       = "Config"
    clusters = [{ name = "ocp", cluster = {
      server                = var.api_server_url
      insecure-skip-tls-verify = true
    }}]
    users = [{ name = "ocp-user", user = { token = var.oc_token }}]
    contexts = [{ name = "default", context = { cluster = "ocp", user = "ocp-user" }}]
    current-context = "default"
  })
}

resource "local_file" "kubeconfig" {
  filename        = "${path.module}/work/kubeconfig"
  file_permission = "0600"
  content         = local.kubeconfig
}

resource "null_resource" "validate_connectivity" {
  depends_on = [local_file.kubeconfig]

  provisioner "local-exec" {
    command = "kubectl --kubeconfig ${local_file.kubeconfig.filename} cluster-info"
  }
}
```

- [ ] **Step 4: Create outputs.tf**

```hcl
output "kubeconfig_content" {
  value     = local.kubeconfig
  sensitive = true
}

output "cluster_name" {
  value = var.cluster_name
}

output "cluster_endpoint" {
  value = var.api_server_url
}
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add infra/ocp/connect module for OpenShift cluster connectivity"
```

---

### Task 15: Create infra/azure/aks module stub

**Files (bnk-forge-modules):**
- Create: `infra/azure/aks/main.tf`
- Create: `infra/azure/aks/variables.tf`
- Create: `infra/azure/aks/outputs.tf`
- Create: `infra/azure/aks/versions.tf`
- Create: `infra/azure/aks/module.json`

This follows the same pattern as `infra/aws/eks` but for Azure AKS. Full implementation depends on Azure provider access, but the structure and interface should be defined.

- [ ] **Step 1-4: Create module files** (same pattern as Task 14 but using `azurerm` provider)

Key variables: `resource_group_name`, `cluster_name`, `location`, `node_count`, `vm_size`, `kubernetes_version`

Key outputs: `kubeconfig_content`, `cluster_name`, `cluster_endpoint`, `resource_group_name`

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add infra/azure/aks module for AKS cluster provisioning"
```

---

## Phase 5: Forge Backend — Platform-Specific Services (bnk-forge-v2)

### Task 16: Add cluster registration services for non-EKS platforms

**Files:**
- Modify: `backend/services/cluster_management_service.py` — add AKS, GKE, OCP registration
- Existing: `backend/services/eks_service.py` (reference, don't modify)

- [ ] **Step 1: Read existing `eks_service.py` to understand the pattern**

The EKS service generates kubeconfig using `aws eks get-token`. Each platform needs an equivalent:
- **AKS**: `az aks get-credentials` or kubeconfig from Azure API
- **GKE**: `gcloud container clusters get-credentials`
- **OCP**: `oc login` token or kubeconfig upload
- **Generic/Ubuntu**: kubeconfig file upload (already supported)

- [ ] **Step 2: Add platform-specific kubeconfig generation to cluster_management_service**

```python
def generate_kubeconfig_for_platform(self, cluster, platform_profile: str) -> str:
    """Generate kubeconfig content based on platform profile."""
    if platform_profile == "eks":
        return self._generate_eks_kubeconfig(cluster)
    elif platform_profile == "aks":
        return self._generate_aks_kubeconfig(cluster)
    elif platform_profile == "gke":
        return self._generate_gke_kubeconfig(cluster)
    elif platform_profile == "ocp":
        return self._generate_ocp_kubeconfig(cluster)
    else:
        return self._get_uploaded_kubeconfig(cluster)
```

- [ ] **Step 3: Implement `_generate_aks_kubeconfig`**

```python
def _generate_aks_kubeconfig(self, cluster) -> str:
    """Generate kubeconfig for AKS using stored credentials."""
    # AKS stores kubeconfig directly — retrieved via Azure API or az CLI
    if cluster.kubeconfig_data:
        return cluster.kubeconfig_data
    raise ValueError("AKS cluster requires kubeconfig_data or az CLI credentials")
```

- [ ] **Step 4: Implement stubs for GKE and OCP**

Similar pattern — use stored kubeconfig or platform CLI.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add multi-platform kubeconfig generation to cluster management service"
```

---

### Task 17: Update stack templates API to expose platform_defaults

**Files:**
- Modify: `backend/routes/stacks.py` — include platform_defaults in template responses
- Modify: `backend/schemas/stacks.py` — add platform_defaults to response schema

- [ ] **Step 1: Add to schema**

```python
class StackTemplateResponse(BaseModel):
    # ... existing fields ...
    platform_defaults: dict | None = None
```

- [ ] **Step 2: Include in route serialization**

The `get_template` already serializes manually (Task 8's branch changes). Add `platform_defaults` to the dict.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: expose platform_defaults in stack template API responses"
```

---

## Phase 6: Frontend — Platform-Aware UI (bnk-forge-v2)

The `agent/platform-awareness` branch has the type system and helpers built (`platform.ts`, `module-engine.ts`, `module-compatibility.ts`, `platform-context.ts`) but 8 key user-facing features are missing. Each task below addresses one.

### Task 18: Project create/edit — platform selector

**Status:** Completely missing. Users cannot set `target_platform_profile` when creating or editing a project.

**Files:**
- Modify: `frontend-v2/src/pages/Projects.tsx` (CreateProjectDialog)
- Modify: `frontend-v2/src/pages/ProjectDetailV2.tsx` (edit flow)
- Modify: `frontend-v2/src/types/projects.ts`

- [ ] **Step 1: Add platform_profile to CreateProjectDialog**

Add a radio group or select field after the cloud_provider field:

```tsx
import { getPlatformProfileLabel } from '@/lib/platform-context';
import type { PlatformProfile } from '@/types/platform';

const PLATFORM_OPTIONS: { value: PlatformProfile; label: string }[] = [
  { value: 'eks', label: 'Amazon EKS' },
  { value: 'aks', label: 'Azure AKS' },
  { value: 'gke', label: 'Google GKE' },
  { value: 'ocp', label: 'OpenShift / OKD' },
  { value: 'generic_onprem', label: 'Generic / On-Prem (kind, microk8s, bare-metal)' },
];
```

Wire the selected platform into the project creation API payload as `target_platform_profile`.

- [ ] **Step 2: Add "Change Platform" option in project edit**

Add an edit dialog/button on ProjectDetailV2 that allows changing `target_platform_profile` with a warning about module compatibility.

- [ ] **Step 3: Auto-infer platform from cloud_provider**

When user selects `cloud_provider: "aws"` → default `target_platform_profile` to `"eks"`, etc.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(frontend): add platform selector to project create/edit"
```

---

### Task 19: Module filtering by platform compatibility

**Status:** Completely missing. Module library page has no platform filter.

**Files:**
- Modify: `frontend-v2/src/pages/Modules.tsx` — add platform filter dropdown
- Uses: `module-compatibility.ts` `getCompatibilitySummary()` (already exists)

- [ ] **Step 1: Add platform filter to Modules page toolbar**

```tsx
const [platformFilter, setPlatformFilter] = useState<PlatformProfile | 'all'>('all');

// Filter modules by platform compatibility
const filteredModules = modules.filter(m => {
  if (platformFilter === 'all') return true;
  const compat = m.platform_compatibility;
  if (!compat || compat.declared_any) return true;
  return compat.supported_profiles?.includes(platformFilter);
});
```

- [ ] **Step 2: Show compatibility badge on module cards in the library grid**

Not just in the detail sheet — show a compact platform tag list on each module card.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(frontend): add platform compatibility filter to module library"
```

---

### Task 20: Blueprint platform composition and deploy-time platform selection

**Status:** Partially done (catalog validation exists). Missing: platform picker at deploy time, compatibility check.

**Files:**
- Modify: `frontend-v2/src/pages/Stacks.tsx` — show platform badges, add platform picker to deploy dialog
- Modify: `frontend-v2/src/types/stacks.ts` — add `platform_defaults` type

- [ ] **Step 1: Add `PlatformDefaults` type and show platform badges on blueprint cards**

```typescript
export interface PlatformDefaults {
  [platform: string]: Record<string, string>;
}
```

Show supported platforms from `platform_defaults` keys as badges on each blueprint card.

- [ ] **Step 2: Add platform picker to StackDetailDialog deploy flow**

When deploying a blueprint with `platform_defaults`, show a platform selector. Selected platform is passed to the API so the backend applies the correct defaults.

- [ ] **Step 3: Show compatibility warnings**

If the project's `target_platform_profile` doesn't match any key in the blueprint's `platform_defaults`, show a warning: "This blueprint has no defaults for [platform]. Variables may need manual adjustment."

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(frontend): add platform selection and compatibility to blueprint deployment"
```

---

### Task 21: Cluster platform detection UI

**Status:** Completely missing. Backend detects platform but no UI to trigger re-detection or show detailed results.

**Files:**
- Modify: `frontend-v2/src/pages/kubernetes/K8sDetailPanel.tsx`
- Modify: `frontend-v2/src/pages/kubernetes/K8sResourceTable.tsx`

- [ ] **Step 1: Display platform profile and capabilities on cluster detail**

```tsx
{cluster.detected_platform_profile && (
  <div>
    <Badge>{getPlatformProfileLabel(cluster.detected_platform_profile)}</Badge>
    {cluster.platform_capabilities &&
      Object.entries(cluster.platform_capabilities)
        .filter(([_, v]) => v === true)
        .map(([k]) => <Badge key={k} variant="outline" size="sm">{k.replace(/_/g, ' ')}</Badge>)
    }
  </div>
)}
```

- [ ] **Step 2: Add "Re-detect Platform" action button**

Button that calls the backend platform detection endpoint and refreshes cluster data.

- [ ] **Step 3: Show target vs detected mismatch alert**

If `project.target_platform_profile !== cluster.detected_platform_profile`, show a prominent amber alert.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(frontend): add cluster platform detection display and re-detect action"
```

---

### Task 22: Pre-execution platform safety checks

**Status:** Completely missing. No warnings before deploying modules to incompatible platforms.

**Files:**
- Modify: `frontend-v2/src/pages/project-detail/ModuleTableRow.tsx`
- Modify: `frontend-v2/src/pages/project-detail/ModuleGroupTable.tsx`

- [ ] **Step 1: Check module compatibility before apply/plan**

When user clicks apply/plan, check `module.platform_compatibility.supported_profiles` against `project.target_platform_profile`. If incompatible, show a confirmation dialog:

"Module [name] is not declared compatible with [platform]. Deploy anyway?"

- [ ] **Step 2: Show capability requirements**

If module requires capabilities the cluster doesn't have (e.g., `sriov: true` but cluster reports `sriov: false`), show a warning in the module row.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(frontend): add pre-execution platform compatibility checks"
```

---

### Task 23: Platform-aware error remediation

**Status:** Backend error mappings exist in types but UI doesn't use them.

**Files:**
- Modify: `frontend-v2/src/components/ErrorState.tsx` or equivalent error display
- Modify: `frontend-v2/src/types/drift.ts` (already has platform error hints)

- [ ] **Step 1: Wire platform context into error display**

When a module deployment fails, check if the error matches known platform-specific patterns:
- SCC errors on OCP → "OpenShift SecurityContextConstraints may need updating"
- IRSA errors on EKS → "Check IAM Role for Service Account configuration"
- Ingress vs Route on OCP → "OpenShift uses Routes instead of Ingress by default"

- [ ] **Step 2: Show remediation steps in error panel**

Add a "Platform Hint" section below error output that maps common failure patterns to platform-specific remediation.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(frontend): add platform-aware error remediation hints"
```

---

### Task 24: Dashboard platform summary

**Status:** Completely missing. No overview of fleet platform health.

**Files:**
- Modify: `frontend-v2/src/pages/Dashboard.tsx` or equivalent home page

- [ ] **Step 1: Add platform overview card to dashboard**

Show a summary of all projects grouped by `target_platform_profile`:
- "3 EKS projects, 1 OCP project, 2 Generic"
- Show any target/detected mismatches as warnings

- [ ] **Step 2: Show cluster platform distribution**

Bar chart or badge list showing platform distribution across registered clusters.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(frontend): add platform summary to dashboard"
```

---

### Task 25: BNK platform requirements warnings

**Status:** Completely missing. No BNK-specific platform prerequisite checks during deployment.

**Files:**
- Modify: `frontend-v2/src/pages/project-detail/ModuleGroupTable.tsx` (stack deploy flow)
- Create: `frontend-v2/src/lib/bnk-platform-requirements.ts`

- [ ] **Step 1: Define BNK platform requirements**

```typescript
export const BNK_PLATFORM_REQUIREMENTS: Record<string, { required: string[]; warnings: string[] }> = {
  eks: {
    required: ['secondary_networks', 'hugepages'],
    warnings: ['EKS requires high-performance nodes with SR-IOV VFs for TMM data plane'],
  },
  ocp: {
    required: ['secondary_networks', 'scc', 'operator_framework'],
    warnings: ['OCP requires SecurityContextConstraints for privileged TMM workloads'],
  },
  generic_onprem: {
    required: ['secondary_networks', 'hugepages'],
    warnings: ['Ensure Multus CNI and SR-IOV device plugin are pre-installed'],
  },
};
```

- [ ] **Step 2: Show requirements check before BNK stack deployment**

When deploying a BNK blueprint, check the cluster's capabilities against `BNK_PLATFORM_REQUIREMENTS[platform]`. Show blocking errors for missing `required` capabilities and amber warnings for `warnings`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(frontend): add BNK platform requirements validation before deployment"
```

---

## Phase 6b: AWS Backward Compatibility (CRITICAL)

The existing AWS blueprints must continue to work after the module changes in Phase 1. The modules now expect `local.forge_kubeconfig` (from `bnk_forge_providers.tf`) instead of `data.aws_eks_cluster`.

### Task 26: Verify existing AWS blueprint flow still works end-to-end

**Files:**
- Read: `backend/services/execution/config_writer.py` — verify AWS path emits `local.forge_kubeconfig`
- Read: `backend/utils/provider_config.py` — verify `generate_aws_eks_providers()` output

- [ ] **Step 1: Trace the AWS deployment path**

For an AWS project deploying `bnk/flo`:
1. `config_writer.write_provider_config()` detects `cloud_provider == "aws"`
2. Calls `generate_aws_eks_providers()` → emits `data.aws_eks_cluster` + `data.aws_eks_cluster_auth`
3. Writes `bnk_forge_providers.tf` into module workspace
4. Module's `main.tf` now uses `try(local.forge_kubeconfig, var.forge_kubeconfig_content)`
5. **GAP**: `local.forge_kubeconfig` is NOT defined in the current `bnk_forge_providers.tf` output

This means existing AWS deployments WILL BREAK unless Phase 2 (Task 8) is completed first.

- [ ] **Step 2: Confirm Phase 2 is a hard prerequisite for deploying updated modules**

The modules repo changes and Forge config_writer changes MUST be deployed together. Document this as a coordinated release requirement.

- [ ] **Step 3: Add integration test**

Create a test that simulates the full AWS path:
1. Mock an AWS project with `cloud_provider="aws"`
2. Call `write_provider_config()` 
3. Verify the output `bnk_forge_providers.tf` contains `local.forge_kubeconfig`
4. Verify it still contains `data.aws_eks_cluster` (needed for dynamic token refresh)

- [ ] **Step 4: Commit**

```bash
git commit -m "test: verify AWS backward compatibility with forge_kubeconfig injection"
```

---

### Task 27: Update existing AWS blueprints to include platform_defaults

The current AWS blueprints (`aws-k8s-foundation`, `f5-bnk-2.2`, `bnk-demo-apps`) must be updated so they work with the new platform_defaults system while remaining backward-compatible.

**Files:**
- Modify: `backend/data/stack_templates.json`

- [ ] **Step 1: Update "f5-bnk-2.2" — change from AWS-only to multi-platform**

Change `cloud_provider` from `"aws"` to `"any"` and add `platform_defaults` with AWS as the default (preserving existing behavior):

```json
{
  "slug": "f5-bnk-2.2",
  "cloud_provider": "any",
  "platform_defaults": {
    "eks": {
      "container_platform": "AWS",
      "storage_class_name": "gp3",
      "cloud_provider": "aws",
      "cni_type": "host-device",
      "auto_lasthop": "AUTO_LASTHOP_ENABLED"
    },
    "aks": {
      "container_platform": "Azure",
      "storage_class_name": "managed-csi",
      "cloud_provider": "azure",
      "cni_type": "sriov",
      "auto_lasthop": "AUTO_LASTHOP_ENABLED"
    },
    "ocp": {
      "container_platform": "Generic",
      "storage_class_name": "ocs-storagecluster-ceph-rbd",
      "cloud_provider": "",
      "cni_type": "sriov",
      "auto_lasthop": ""
    },
    "generic_onprem": {
      "container_platform": "Generic",
      "storage_class_name": "local-path",
      "cloud_provider": "",
      "cni_type": "sriov",
      "auto_lasthop": ""
    }
  }
}
```

- [ ] **Step 2: Keep "aws-k8s-foundation" as AWS-only**

This blueprint provisions AWS infrastructure (VPC, EKS, IAM) — it's inherently AWS-specific. Keep `cloud_provider: "aws"`, no `platform_defaults` needed. Add a tag: `"platform_locked": true`.

- [ ] **Step 3: Update "bnk-on-k8s" — add explicit platform_defaults**

Already `"any"` but should have explicit platform_defaults so users see what variables change per platform.

- [ ] **Step 4: Update "bnk-demo-apps"**

Keep AWS Bedrock config as the `eks` default. For other platforms, note in description that AI features require manual config.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: update existing blueprints with platform_defaults, preserve AWS backward compat"
```

---

## Phase 7: New Stack Templates for Each Platform (bnk-forge-v2)

### Task 28: Create platform-specific infrastructure blueprints

**Files:**
- Modify: `backend/data/stack_templates.json`

- [ ] **Step 1: Add "Ubuntu/Kind Development Foundation" template**

```json
{
  "name": "Ubuntu Kind Development Foundation",
  "slug": "ubuntu-kind-foundation",
  "description": "Provisions a kind Kubernetes cluster on Ubuntu for BNK development and testing",
  "category": "infrastructure",
  "cloud_provider": "any",
  "icon": "server",
  "color": "#E95420",
  "estimated_time": "10 minutes",
  "difficulty": "beginner",
  "modules": [
    {
      "path": "infra/ubuntu/kind",
      "name": "Kind Cluster",
      "variables": {
        "cluster_name": "bnk-dev",
        "kubernetes_version": "1.29.2",
        "worker_nodes": 2
      }
    }
  ],
  "tags": ["ubuntu", "kind", "development", "on-prem"],
  "maturity": "beta"
}
```

- [ ] **Step 2: Add "OpenShift Connection" template**

```json
{
  "name": "OpenShift Cluster Connection",
  "slug": "ocp-connection",
  "description": "Connect to an existing OpenShift cluster for BNK deployment",
  "category": "infrastructure",
  "cloud_provider": "any",
  "icon": "openshift",
  "color": "#EE0000",
  "difficulty": "intermediate",
  "modules": [
    {
      "path": "infra/ocp/connect",
      "name": "OCP Connection",
      "variables": {}
    }
  ],
  "tags": ["openshift", "ocp", "enterprise"],
  "maturity": "beta"
}
```

- [ ] **Step 3: Add "Azure AKS Foundation" template**

```json
{
  "name": "Azure AKS Foundation",
  "slug": "azure-aks-foundation",
  "description": "Provisions Azure AKS cluster with networking and storage for BNK",
  "category": "infrastructure",
  "cloud_provider": "azure",
  "icon": "cloud",
  "color": "#0078D4",
  "difficulty": "intermediate",
  "modules": [
    {
      "path": "infra/azure/aks",
      "name": "AKS Cluster",
      "variables": {}
    }
  ],
  "tags": ["azure", "aks", "cloud"],
  "maturity": "alpha"
}
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: add Ubuntu/kind, OCP, and Azure infrastructure blueprints"
```

---

## Execution Order Summary

| Phase | Repo | Branch | Tasks | Dependency |
|-------|------|--------|-------|------------|
| 1 | bnk-forge-modules | release/2.2 | 1-7 | None — can start immediately |
| 2 | bnk-forge-v2 | agent/platform-awareness | 8-9 | Phase 1 (modules expect injected kubeconfig) |
| 3 | bnk-forge-v2 | agent/platform-awareness | 10-12 | Phase 2 |
| 4 | bnk-forge-modules | release/2.2 | 13-15 | None — independent of phases 2-3 |
| 5 | bnk-forge-v2 | agent/platform-awareness | 16-17 | Phase 3 |
| 6 | bnk-forge-v2 | agent/platform-awareness | 18-25 | Phase 5 (8 frontend tasks) |
| 6b | bnk-forge-v2 | agent/platform-awareness | 26-27 | Phase 2 (AWS backward compat) |
| 7 | bnk-forge-v2 | agent/platform-awareness | 28 | Phase 4 + Phase 6b |

**Phases 1 and 4 can run in parallel** (both in modules repo, independent concerns).
**Phase 6b is CRITICAL** — must be done before releasing Phase 1 module changes to production.
**Phase 6 frontend tasks** (18-25) can be done incrementally after Phase 5.

### Coordinated Release Requirement

The modules repo changes (Phase 1) and Forge config_writer changes (Phase 2) MUST be deployed together.
Deploying updated modules without the Forge-side `local.forge_kubeconfig` injection will break all deployments.

**Safe deployment order:**
1. Deploy Forge changes (Phase 2) first — adds `local.forge_kubeconfig` to `bnk_forge_providers.tf`
2. Then update module source to new release/2.2 — modules now consume `local.forge_kubeconfig`
3. Existing AWS deployments continue working because `local.forge_kubeconfig` is built from same `data.aws_eks_cluster` data sources

---

## Frontend Feature Tracker (Phase 6 — Tasks 18-25)

| # | Feature | Task | Status |
|---|---------|------|--------|
| 1 | Platform selector in project create/edit | Task 18 | Missing |
| 2 | Module filtering by platform | Task 19 | Missing |
| 3 | Blueprint platform composition + deploy-time platform picker | Task 20 | Missing |
| 4 | Cluster "Re-detect Platform" action + capability display | Task 21 | Missing |
| 5 | Pre-execution platform safety checks | Task 22 | Missing |
| 6 | Platform-aware error remediation | Task 23 | Missing |
| 7 | Dashboard platform summary | Task 24 | Missing |
| 8 | BNK platform requirements warnings | Task 25 | Missing |

---

## Out of Scope (Future Work)

- GCP/GKE infrastructure module (`infra/gcp/gke`) — needs GCP provider access
- AI backend alternatives (Azure OpenAI, Vertex AI, Ollama) — needs separate GenAI work
- OCP SecurityContextConstraint modules — needs OCP cluster for testing
- DPU support per platform — depends on hardware availability
- Benchmark targets per platform — depends on platform-specific performance profiles
