"""
Provider configuration utilities for BNK-Forge.

Generates Terraform/OpenTofu provider configurations for various cloud providers.
Handles version-specific syntax differences (e.g., Helm 2.x vs 3.x).
"""

import logging
import os
import re

logger = logging.getLogger(__name__)


def normalize_cloud_provider(value: str | None) -> str | None:
    """Canonicalize a cloud_provider string to lowercase so 'IBM' == 'ibm'.

    cloud_provider values are stored and compared lowercase by convention;
    normalize on write (and defensively on compare) so case never matters.
    Returns None for empty/blank input.
    """
    if not value:
        return None
    return value.strip().lower() or None


def detect_helm_version_syntax(versions_tf_path: str) -> bool:
    """
    Detect whether to use Helm 3.x assignment syntax based on versions.tf.

    Helm 2.x uses block syntax: kubernetes { }
    Helm 3.x uses assignment syntax: kubernetes = { }

    Args:
        versions_tf_path: Path to versions.tf file

    Returns:
        True if assignment syntax should be used (Helm 3.x), False for block syntax (Helm 2.x)
    """
    if not os.path.exists(versions_tf_path):
        return False  # Default to Helm 2.x syntax

    with open(versions_tf_path) as f:
        content = f.read()

    # Look for helm provider block and extract its version constraint
    # Use non-greedy matching and look specifically within the helm block
    # Pattern: helm = { ... version = "X.Y.Z" ... }
    helm_block_match = re.search(
        r'helm\s*=\s*\{[^}]*version\s*=\s*"([^"]+)"',
        content,
        re.IGNORECASE
    )
    if helm_block_match:
        version_constraint = helm_block_match.group(1)
        logger.debug(f"Found Helm version constraint: '{version_constraint}'")

        # If constraint allows 3.x (>= without upper bound, or explicit 3.x)
        if '>= 2' in version_constraint and '~>' not in version_constraint:
            # >= 2.x without tilde could install 3.x
            logger.debug(f"Helm version constraint '{version_constraint}' may install 3.x, using assignment syntax")
            return True
        elif '>= 3' in version_constraint or '~> 3' in version_constraint:
            logger.debug(f"Helm version constraint '{version_constraint}' requires 3.x, using assignment syntax")
            return True
        elif '~> 2' in version_constraint:
            logger.debug(f"Helm version constraint '{version_constraint}' pins to 2.x, using block syntax")
            return False

    return False  # Default to Helm 2.x block syntax


def generate_helm_kubernetes_block(
    use_assignment_syntax: bool,
    host: str,
    cluster_ca_certificate: str,
    token: str = None,
    client_certificate: str = None,
    client_key: str = None,
    config_path: str = None,
    config_context: str = None
) -> str:
    """
    Generate the kubernetes configuration block for Helm provider.

    Args:
        use_assignment_syntax: True for Helm 3.x (kubernetes = {}), False for Helm 2.x (kubernetes {})
        host: Kubernetes API server host
        cluster_ca_certificate: Base64-decoded CA certificate expression
        token: Token expression (for EKS/GKE)
        client_certificate: Client cert expression (for AKS)
        client_key: Client key expression (for AKS)
        config_path: Path to kubeconfig file (for generic/on-prem)
        config_context: Kubeconfig context (for generic/on-prem)

    Returns:
        Helm provider configuration string
    """
    if config_path:
        # Generic kubeconfig-based configuration
        context_line = f'    config_context = "{config_context}"' if config_context else ""
        if use_assignment_syntax:
            return f'''
provider "helm" {{
  kubernetes = {{
    config_path = "{config_path}"
    {context_line}
  }}
}}
'''
        else:
            return f'''
provider "helm" {{
  kubernetes {{
    config_path = "{config_path}"
    {context_line}
  }}
}}
'''

    # Token-based auth (EKS, GKE)
    if token:
        if use_assignment_syntax:
            return f'''
provider "helm" {{
  kubernetes = {{
    host                   = {host}
    cluster_ca_certificate = {cluster_ca_certificate}
    token                  = {token}
  }}
}}
'''
        else:
            return f'''
provider "helm" {{
  kubernetes {{
    host                   = {host}
    cluster_ca_certificate = {cluster_ca_certificate}
    token                  = {token}
  }}
}}
'''

    # Certificate-based auth (AKS)
    if client_certificate and client_key:
        if use_assignment_syntax:
            return f'''
provider "helm" {{
  kubernetes = {{
    host                   = {host}
    client_certificate     = {client_certificate}
    client_key             = {client_key}
    cluster_ca_certificate = {cluster_ca_certificate}
  }}
}}
'''
        else:
            return f'''
provider "helm" {{
  kubernetes {{
    host                   = {host}
    client_certificate     = {client_certificate}
    client_key             = {client_key}
    cluster_ca_certificate = {cluster_ca_certificate}
  }}
}}
'''

    raise ValueError("Must provide either token, client_certificate+client_key, or config_path")


def _generate_helm_kubernetes_exec_block(
    use_assignment_syntax: bool,
    cluster_name: str,
    aws_region: str,
) -> str:
    """
    Generate Helm provider block with exec-based auth for AWS EKS.

    Uses aws eks get-token so tokens are minted at operation time (not baked in at plan).
    """
    if use_assignment_syntax:
        # Helm 3.x: kubernetes = { ... exec = { ... } } (all assignment syntax)
        # exec must also use assignment syntax inside an assignment-syntax kubernetes block.
        return f'''
provider "helm" {{
  kubernetes = {{
    host                   = data.aws_eks_cluster.cluster.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.cluster.certificate_authority[0].data)
    exec = {{
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", "{cluster_name}", "--region", "{aws_region}"]
    }}
  }}
}}
'''
    else:
        # Helm 2.x: kubernetes { exec { ... } } (block syntax)
        return f'''
provider "helm" {{
  kubernetes {{
    host                   = data.aws_eks_cluster.cluster.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.cluster.certificate_authority[0].data)
    exec {{
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", "{cluster_name}", "--region", "{aws_region}"]
    }}
  }}
}}
'''


def generate_aws_eks_providers(
    cluster_name: str,
    aws_region: str,
    required_providers: list[str],
    helm_use_assignment_syntax: bool = False
) -> tuple[list[str], list[str]]:
    """
    Generate AWS EKS provider configuration.

    Args:
        cluster_name: EKS cluster name
        aws_region: AWS region
        required_providers: List of required providers (e.g., ['kubernetes', 'helm'])
        helm_use_assignment_syntax: Use Helm 3.x syntax

    Returns:
        Tuple of (provider_blocks, data_sources)
    """
    provider_blocks = []
    data_sources = []

    # AWS provider
    provider_blocks.append(f'''
provider "aws" {{
  region = "{aws_region}"
}}
''')

    # EKS data source (cluster metadata only — no aws_eks_cluster_auth; tokens minted via exec)
    data_sources.append(f'''
data "aws_eks_cluster" "cluster" {{
  name = "{cluster_name}"
}}
''')

    # Kubernetes provider — exec auth so tokens are minted fresh on each operation
    if 'kubernetes' in required_providers:
        provider_blocks.append(f'''
provider "kubernetes" {{
  host                   = data.aws_eks_cluster.cluster.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.cluster.certificate_authority[0].data)
  exec {{
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", "{cluster_name}", "--region", "{aws_region}"]
  }}
}}
''')

    # Helm provider — exec auth (same pattern as kubernetes provider)
    if 'helm' in required_providers:
        helm_config = _generate_helm_kubernetes_exec_block(
            use_assignment_syntax=helm_use_assignment_syntax,
            cluster_name=cluster_name,
            aws_region=aws_region,
        )
        provider_blocks.append(helm_config)

    return provider_blocks, data_sources


def generate_azure_aks_providers(
    cluster_name: str,
    resource_group: str,
    required_providers: list[str],
    helm_use_assignment_syntax: bool = False
) -> tuple[list[str], list[str]]:
    """
    Generate Azure AKS provider configuration.

    Args:
        cluster_name: AKS cluster name
        resource_group: Azure resource group
        required_providers: List of required providers
        helm_use_assignment_syntax: Use Helm 3.x syntax

    Returns:
        Tuple of (provider_blocks, data_sources)
    """
    provider_blocks = []
    data_sources = []

    # Azure provider
    provider_blocks.append('''
provider "azurerm" {
  features {}
}
''')

    # AKS data source
    data_sources.append(f'''
data "azurerm_kubernetes_cluster" "cluster" {{
  name                = "{cluster_name}"
  resource_group_name = "{resource_group}"
}}
''')

    # Kubernetes provider
    if 'kubernetes' in required_providers:
        provider_blocks.append('''
provider "kubernetes" {
  host                   = data.azurerm_kubernetes_cluster.cluster.kube_config[0].host
  client_certificate     = base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].client_certificate)
  client_key             = base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].client_key)
  cluster_ca_certificate = base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].cluster_ca_certificate)
}
''')

    # Helm provider
    if 'helm' in required_providers:
        helm_config = generate_helm_kubernetes_block(
            use_assignment_syntax=helm_use_assignment_syntax,
            host='data.azurerm_kubernetes_cluster.cluster.kube_config[0].host',
            cluster_ca_certificate='base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].cluster_ca_certificate)',
            client_certificate='base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].client_certificate)',
            client_key='base64decode(data.azurerm_kubernetes_cluster.cluster.kube_config[0].client_key)'
        )
        provider_blocks.append(helm_config)

    return provider_blocks, data_sources


def generate_gcp_gke_providers(
    cluster_name: str,
    gcp_project: str,
    gcp_region: str,
    required_providers: list[str],
    helm_use_assignment_syntax: bool = False
) -> tuple[list[str], list[str]]:
    """
    Generate GCP GKE provider configuration.

    Args:
        cluster_name: GKE cluster name
        gcp_project: GCP project ID
        gcp_region: GCP region
        required_providers: List of required providers
        helm_use_assignment_syntax: Use Helm 3.x syntax

    Returns:
        Tuple of (provider_blocks, data_sources)
    """
    provider_blocks = []
    data_sources = []

    # GCP provider
    provider_blocks.append(f'''
provider "google" {{
  project = "{gcp_project}"
  region  = "{gcp_region}"
}}
''')

    # GKE data sources
    data_sources.append(f'''
data "google_container_cluster" "cluster" {{
  name     = "{cluster_name}"
  location = "{gcp_region}"
}}

data "google_client_config" "default" {{}}
''')

    # Kubernetes provider
    if 'kubernetes' in required_providers:
        provider_blocks.append('''
provider "kubernetes" {
  host                   = "https://${data.google_container_cluster.cluster.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(data.google_container_cluster.cluster.master_auth[0].cluster_ca_certificate)
}
''')

    # Helm provider
    if 'helm' in required_providers:
        helm_config = generate_helm_kubernetes_block(
            use_assignment_syntax=helm_use_assignment_syntax,
            host='"https://${data.google_container_cluster.cluster.endpoint}"',
            cluster_ca_certificate='base64decode(data.google_container_cluster.cluster.master_auth[0].cluster_ca_certificate)',
            token='data.google_client_config.default.access_token'
        )
        provider_blocks.append(helm_config)

    return provider_blocks, data_sources


def generate_ibm_roks_providers(
    cluster_name: str,
    ibm_region: str,
    required_providers: list[str],
    resource_group_id: str | None = None,
    helm_use_assignment_syntax: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Generate IBM ROKS provider configuration.

    Args:
        cluster_name: ROKS cluster name or ID
        ibm_region: IBM Cloud region
        required_providers: List of required providers
        resource_group_id: Optional IBM resource group ID
        helm_use_assignment_syntax: Use Helm 3.x syntax

    Returns:
        Tuple of (provider_blocks, data_sources)
    """
    provider_blocks = []
    data_sources = []

    provider_blocks.append(f'''
provider "ibm" {{
  region = "{ibm_region}"
}}
''')

    resource_group_line = f'  resource_group_id = "{resource_group_id}"\n' if resource_group_id else ""
    data_sources.append(f'''
data "ibm_container_cluster_config" "cluster" {{
  cluster_name_id = "{cluster_name}"
{resource_group_line}}}
''')

    if 'kubernetes' in required_providers:
        provider_blocks.append('''
provider "kubernetes" {
  host                   = data.ibm_container_cluster_config.cluster.host
  token                  = data.ibm_container_cluster_config.cluster.token
  cluster_ca_certificate = data.ibm_container_cluster_config.cluster.ca_certificate
}
''')

    if 'helm' in required_providers:
        helm_config = generate_helm_kubernetes_block(
            use_assignment_syntax=helm_use_assignment_syntax,
            host='data.ibm_container_cluster_config.cluster.host',
            cluster_ca_certificate='data.ibm_container_cluster_config.cluster.ca_certificate',
            token='data.ibm_container_cluster_config.cluster.token'
        )
        provider_blocks.append(helm_config)

    return provider_blocks, data_sources


def generate_generic_providers(
    kubeconfig_path: str,
    k8s_context: str | None,
    required_providers: list[str],
    helm_use_assignment_syntax: bool = False
) -> tuple[list[str], list[str]]:
    """
    Generate generic kubeconfig-based provider configuration.

    Args:
        kubeconfig_path: Path to kubeconfig file
        k8s_context: Kubernetes context name (optional)
        required_providers: List of required providers
        helm_use_assignment_syntax: Use Helm 3.x syntax

    Returns:
        Tuple of (provider_blocks, data_sources)
    """
    provider_blocks = []
    data_sources = []

    config_context = f'config_context = "{k8s_context}"' if k8s_context else ""

    # Kubernetes provider
    if 'kubernetes' in required_providers:
        provider_blocks.append(f'''
provider "kubernetes" {{
  config_path = "{kubeconfig_path}"
  {config_context}
}}
''')

    # Helm provider
    if 'helm' in required_providers:
        helm_config = generate_helm_kubernetes_block(
            use_assignment_syntax=helm_use_assignment_syntax,
            host=None,
            cluster_ca_certificate=None,
            config_path=kubeconfig_path,
            config_context=k8s_context
        )
        provider_blocks.append(helm_config)

    return provider_blocks, data_sources


def generate_forge_kubeconfig_local(
    cloud_provider: str,
    variables: dict | None = None,
    project=None,
    kubeconfig_path: str | None = None,
) -> str:
    """
    Generate a Terraform locals block that provides `local.forge_kubeconfig`.

    This kubeconfig YAML content is consumed by modules via:
        try(local.forge_kubeconfig, var.forge_kubeconfig_content)

    For cloud providers, it builds kubeconfig from data source attributes
    (dynamic token refresh on each plan/apply). For generic/on-prem, it
    reads from the uploaded kubeconfig file.

    Args:
        cloud_provider: "aws", "azure", "gcp", "ibm", or "" (generic/on-prem)
        variables: Assembled module variables dict
        project: Project model instance

    Returns:
        HCL string defining locals { forge_kubeconfig = ... }, or ""
    """
    variables = variables or {}

    if cloud_provider == "aws":
        # AWS EKS: build kubeconfig using exec auth so tokens are minted at kubectl operation time.
        # cluster_name and region are resolved from variables / project at generation time.
        cluster_name = (
            (variables.get("cluster_name") or variables.get("eks_cluster_name") or "")
            if variables else ""
        )
        aws_region = (
            (variables.get("region") or variables.get("aws_region") or
             (project.region if project else None) or "")
            if variables or project else ""
        )
        cluster_arg = cluster_name or "${data.aws_eks_cluster.cluster.name}"
        region_arg = aws_region or "${var.region}"
        return f'''
# Platform-agnostic kubeconfig for kubectl local-exec provisioners
# Consumed by modules via: try(local.forge_kubeconfig, var.forge_kubeconfig_content)
# Uses exec auth so tokens are minted fresh on each kubectl invocation (no 15-minute expiry).
locals {{
  forge_kubeconfig = yamlencode({{
    apiVersion      = "v1"
    kind            = "Config"
    clusters        = [{{ name = "cluster", cluster = {{
      server                     = data.aws_eks_cluster.cluster.endpoint
      certificate-authority-data = data.aws_eks_cluster.cluster.certificate_authority[0].data
    }}}}]
    users           = [{{ name = "user", user = {{ exec = {{
      apiVersion = "client.authentication.k8s.io/v1beta1"
      command    = "aws"
      args       = ["eks", "get-token", "--cluster-name", "{cluster_arg}", "--region", "{region_arg}"]
    }} }} }}]
    contexts        = [{{ name = "default", context = {{ cluster = "cluster", user = "user" }} }}]
    current-context = "default"
  }})
}}
'''

    elif cloud_provider == "azure":
        # Azure AKS: use kube_config_raw from azurerm_kubernetes_cluster
        return '''
# Platform-agnostic kubeconfig for kubectl local-exec provisioners
locals {
  forge_kubeconfig = data.azurerm_kubernetes_cluster.cluster.kube_config_raw
}
'''

    elif cloud_provider == "gcp":
        # GCP GKE: build kubeconfig from google_container_cluster data source
        return '''
# Platform-agnostic kubeconfig for kubectl local-exec provisioners
locals {
  forge_kubeconfig = yamlencode({
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
'''

    elif cloud_provider == "ibm":
        # IBM ROKS: build kubeconfig from ibm_container_cluster_config data source
        return '''
# Platform-agnostic kubeconfig for kubectl local-exec provisioners
locals {
  forge_kubeconfig = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    clusters        = [{ name = "cluster", cluster = {
      server                     = data.ibm_container_cluster_config.cluster.host
      certificate-authority-data = base64encode(data.ibm_container_cluster_config.cluster.ca_certificate)
    }}]
    users           = [{ name = "user", user = { token = data.ibm_container_cluster_config.cluster.token }}]
    contexts        = [{ name = "default", context = { cluster = "cluster", user = "user" }}]
    current-context = "default"
  })
}
'''

    else:
        # Generic/on-prem: read kubeconfig from file
        kubeconfig_path = kubeconfig_path or variables.get('kubeconfig_path') or "/app/kubeconfig"
        return f'''
# Platform-agnostic kubeconfig for kubectl local-exec provisioners
locals {{
  forge_kubeconfig = file("{kubeconfig_path}")
}}
'''
