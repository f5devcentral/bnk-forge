"""
Kubeconfig portability normalizer.

Invariant: every kubeconfig stored in KubernetesCluster.kubeconfig_encrypted must be
portable across container boundaries — no file-path references, no unsupported exec
plugins.

Specifically:
  - clusters[].cluster: ``certificate-authority-data`` (base64 inline) present;
    ``certificate-authority`` (file path) absent.
  - users[].user: ``client-certificate-data`` / ``client-key-data`` inline; path forms
    (``client-certificate``, ``client-key``) absent. ``token`` inline; ``tokenFile`` absent.
  - users[].user.exec.command: must be one of SUPPORTED_EXEC_COMMANDS.

Precedence rule: when both ``*-data`` and the path form are present, ``*-data`` wins
and the path form is dropped silently. This is a normalization, not a rejection.

SUPPORTED_EXEC_COMMANDS lists the exec plugins that Forge's worker containers ship.
Any other command causes KubeconfigUnportableError regardless of source.
"""
import logging
from enum import Enum
from typing import Any

import yaml

from core.errors import AppError

logger = logging.getLogger(__name__)

SUPPORTED_EXEC_COMMANDS: frozenset[str] = frozenset(
    {"aws", "aws-iam-authenticator", "gke-gcloud-auth-plugin", "kubelogin"}
)


class NormalizationSource(Enum):
    MANUAL_UPLOAD = "manual_upload"
    SSH_DISCOVERY = "ssh_discovery"
    CLOUD_API_GENERATED = "cloud_api"
    MODULE_OUTPUT = "module_output"
    INTERNAL_REREAD = "internal_reread"


class KubeconfigUnportableError(AppError):
    """Raised when a kubeconfig contains file-path refs or unsupported exec plugins."""

    def __init__(self, field: str, user_message: str):
        super().__init__(
            code="KUBECONFIG_UNPORTABLE",
            message=f"Kubeconfig is not portable: {field}",
            status_code=422,
            details={"field": field, "user_message": user_message},
        )


def normalize_kubeconfig(
    yaml_text: str,
    *,
    source: NormalizationSource,
) -> str:
    """Return the normalized kubeconfig YAML, or raise KubeconfigUnportableError.

    Normalizes all clusters and all users in the file, not just current-context.
    ``*-data`` forms take precedence over file-path forms when both are present
    (path is dropped silently). exec blocks are validated against SUPPORTED_EXEC_COMMANDS.

    For MANUAL_UPLOAD: raises KubeconfigUnportableError on any file-path ref or
    unsupported exec — the caller's API route lets this propagate to a 422.

    For SSH_DISCOVERY: same raise semantics; the caller wraps in a job error.

    For CLOUD_API_GENERATED / MODULE_OUTPUT: same raise, but the caller logs
    ``kubeconfig_invariant_violation`` (grep tag) — this indicates our own pipeline
    produced a non-portable kubeconfig.

    For INTERNAL_REREAD: same raise; a legacy DB row survived pre-fix; user_message
    instructs re-upload.
    """
    try:
        kubeconfig = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise KubeconfigUnportableError(
            field="kubeconfig",
            user_message=f"Kubeconfig is not valid YAML: {exc}",
        )

    if not isinstance(kubeconfig, dict):
        raise KubeconfigUnportableError(
            field="kubeconfig",
            user_message="Kubeconfig must be a YAML mapping, got a non-dict value.",
        )

    _normalize_clusters(kubeconfig, source)
    _normalize_users(kubeconfig, source)

    return yaml.dump(kubeconfig, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REREAD_RE_UPLOAD = (
    "This kubeconfig was stored before Forge enforced portability. "
    "Please re-upload a flattened kubeconfig (run `kubectl config view "
    "--flatten --minify --raw` or `oc config view --flatten --minify --raw` "
    "on the source machine)."
)


def _path_user_message(field: str, path: str, source: NormalizationSource) -> str:
    if source == NormalizationSource.INTERNAL_REREAD:
        return _REREAD_RE_UPLOAD
    return (
        f"Kubeconfig references a file path Forge cannot read (`{path}`). "
        f"On the machine that has access to this file, run "
        f"`kubectl config view --flatten --minify --raw` (or "
        f"`oc config view --flatten --minify --raw`) and upload the output. "
        f"If Forge can reach the cluster via SSH, configure SSH auto-discovery "
        f"and Forge will flatten the kubeconfig remotely."
    )


def _exec_user_message(command: str, source: NormalizationSource) -> str:
    supported = ", ".join(sorted(SUPPORTED_EXEC_COMMANDS))
    if source == NormalizationSource.INTERNAL_REREAD:
        return (
            f"This kubeconfig was stored with an unsupported exec plugin "
            f"(`{command}`). Supported commands: {supported}. "
            "Please re-upload a kubeconfig that uses a bearer token or "
            "client certificate, or one of the supported exec plugins."
        )
    return (
        f"Kubeconfig contains an `exec:` plugin (`{command}`) Forge does not "
        f"support. Supported commands: {supported}. "
        "To deploy with this cluster, replace the exec auth with a bearer "
        "`token` or a `*-data`-inlined client cert/key, then re-upload."
    )


def _normalize_clusters(kubeconfig: dict[str, Any], source: NormalizationSource) -> None:
    """Inline certificate-authority refs; drop path form silently when -data is also present."""
    for entry in kubeconfig.get("clusters") or []:
        cluster = (entry or {}).get("cluster")
        if not isinstance(cluster, dict):
            continue

        has_data = bool(cluster.get("certificate-authority-data"))
        has_path = bool(cluster.get("certificate-authority"))

        if has_data:
            # -data wins; drop the path silently
            cluster.pop("certificate-authority", None)
        elif has_path:
            path = cluster["certificate-authority"]
            raise KubeconfigUnportableError(
                field="certificate-authority",
                user_message=_path_user_message("certificate-authority", path, source),
            )
        # Neither present: allowed (e.g. insecure-skip-tls-verify: true)


def _normalize_users(kubeconfig: dict[str, Any], source: NormalizationSource) -> None:
    """Inline client-cert, client-key, and token refs; validate exec blocks."""
    for entry in kubeconfig.get("users") or []:
        user = (entry or {}).get("user")
        if not isinstance(user, dict):
            continue

        # client-certificate
        if user.get("client-certificate-data"):
            user.pop("client-certificate", None)
        elif user.get("client-certificate"):
            path = user.pop("client-certificate")
            raise KubeconfigUnportableError(
                field="client-certificate",
                user_message=_path_user_message("client-certificate", path, source),
            )

        # client-key
        if user.get("client-key-data"):
            user.pop("client-key", None)
        elif user.get("client-key"):
            path = user.pop("client-key")
            raise KubeconfigUnportableError(
                field="client-key",
                user_message=_path_user_message("client-key", path, source),
            )

        # tokenFile → inline token
        if user.get("token"):
            user.pop("tokenFile", None)
        elif user.get("tokenFile"):
            path = user.pop("tokenFile")
            raise KubeconfigUnportableError(
                field="tokenFile",
                user_message=_path_user_message("tokenFile", path, source),
            )

        # exec plugin allowlist
        exec_block = user.get("exec")
        if isinstance(exec_block, dict):
            command = exec_block.get("command", "")
            if command not in SUPPORTED_EXEC_COMMANDS:
                raise KubeconfigUnportableError(
                    field="exec.command",
                    user_message=_exec_user_message(command, source),
                )


# ---------------------------------------------------------------------------
# SSH-tunnel rewrite (#7)
# ---------------------------------------------------------------------------

def rewrite_kubeconfig_for_tunnel(yaml_text: str, tunnel_port: int) -> str:
    """Point every cluster in a kubeconfig at a local SSH tunnel -- with TLS on.

    The tunnel listens on 127.0.0.1:<port>, but the API server's certificate is
    valid for its real hostname/IPs, not for 127.0.0.1. Both tunnel paths (the
    OpenTofu provider via config_writer, the in-process clients via
    cluster_utils) used to solve that by setting insecure-skip-tls-verify and
    stripping the CA -- which does not fix the hostname mismatch so much as
    disable verification entirely, leaving a tunnelled plan/apply with no
    protection against a MITM on the tunnel path.

    kubeconfig has the right tool for this: `tls-server-name` sets the SNI /
    verification hostname independently of the address dialled. So we keep the
    original CA, keep verification ON, and tell the client to verify against
    the ORIGINAL hostname while dialling the tunnel. This is what the fix
    proposed in #7 did for the Terraform provider (`tls_server_name`), applied
    at the kubeconfig layer that both consumers now share.

    Fail-safe rule: verification can only be RESTORED, never invented. If the
    original entry carried no CA (or was itself insecure-skip-tls-verify), or
    the server URL has no usable hostname, we fall back to the previous
    behaviour so an existing working cluster never stops connecting.

    Uses 127.0.0.1 explicitly, not "localhost": the latter resolves to both ::1
    and 127.0.0.1, the tunnel listener is IPv4-only, and httpx/kr8s try ::1
    first and give up rather than falling back.
    """
    from urllib.parse import urlparse

    doc = yaml.safe_load(yaml_text) or {}
    for entry in doc.get("clusters", []) or []:
        cluster = entry.get("cluster") if isinstance(entry, dict) else None
        if not isinstance(cluster, dict):
            continue

        original_server = str(cluster.get("server") or "")
        original_host = urlparse(original_server).hostname if original_server else None
        has_ca = bool(cluster.get("certificate-authority-data") or cluster.get("certificate-authority"))
        was_insecure = bool(cluster.get("insecure-skip-tls-verify"))

        cluster["server"] = f"https://127.0.0.1:{tunnel_port}"

        if has_ca and original_host and not was_insecure:
            # Restore verification: verify against the real hostname while
            # dialling the tunnel. CA stays; skip flag must NOT be set.
            cluster["tls-server-name"] = original_host
            cluster.pop("insecure-skip-tls-verify", None)
        else:
            # Nothing to verify against -- keep the legacy behaviour so a
            # cluster that worked yesterday still works today.
            cluster["insecure-skip-tls-verify"] = True
            cluster.pop("certificate-authority-data", None)
            cluster.pop("certificate-authority", None)
            cluster.pop("tls-server-name", None)

    return yaml.dump(doc, default_flow_style=False)
