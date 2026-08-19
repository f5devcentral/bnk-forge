"""
Cluster Auto-Registration Service.

Automatically registers Kubernetes clusters from module outputs that match
the cluster-output contract. This is output-contract-driven, not module-name-driven.

Output contract for auto-registration:
- cluster_name (str): Display name for the cluster
- remote_kubeconfig_path (str): Path on remote host where kubeconfig is stored
- remote_host (str): SSH host to fetch kubeconfig from

Any module producing these outputs triggers auto-registration after successful apply.
"""

import logging
import re
from typing import Any

import yaml
from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from models import KubernetesCluster, Project, ProjectModule, SSHCredential
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.platform_context_service import PlatformContextService

logger = logging.getLogger(__name__)


# Required output keys for auto-registration contract
REQUIRED_OUTPUT_KEYS = {"cluster_name", "remote_kubeconfig_path", "remote_host"}


def matches_cluster_output_contract(outputs: dict[str, Any] | None) -> bool:
    """Check if module outputs match the cluster auto-registration contract."""
    if not outputs:
        return False
    return REQUIRED_OUTPUT_KEYS.issubset(outputs.keys())


def maybe_auto_register_cluster(
    db: Session,
    module: ProjectModule,
    outputs: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Attempt to auto-register a cluster from module outputs.

    Returns registration result dict if successful, None if contract not matched
    or registration skipped/failed.

    Output contract:
      Required: cluster_name, remote_kubeconfig_path, remote_host
      Optional: ssh_credential_id (overrides project.ssh_credential_id for bare-metal hosts)
    """
    if not matches_cluster_output_contract(outputs):
        return None

    cluster_name = outputs.get("cluster_name")
    remote_kubeconfig_path = outputs.get("remote_kubeconfig_path")
    remote_host = outputs.get("remote_host")
    # Optional: explicit SSH credential ID (for bare-metal hosts with own credentials)
    ssh_credential_id_override = outputs.get("ssh_credential_id")

    if not all([cluster_name, remote_kubeconfig_path, remote_host]):
        logger.debug(
            "Module %s outputs match contract keys but have empty values, skipping auto-registration",
            module.id,
        )
        return None

    project = module.project
    if not project:
        logger.warning("Module %s has no project, skipping cluster auto-registration", module.id)
        return None

    # Check if cluster with this name already exists in the project
    existing = (
        db.query(KubernetesCluster)
        .filter(
            KubernetesCluster.project_id == project.id,
            KubernetesCluster.name == cluster_name,
        )
        .first()
    )

    if existing:
        logger.info(
            "Cluster '%s' already exists in project %s (id=%s), updating kubeconfig",
            cluster_name,
            project.id,
            existing.id,
        )
        return _update_cluster_from_remote(
            db, existing, project, remote_host, remote_kubeconfig_path,
            ssh_credential_id_override=ssh_credential_id_override,
        )

    logger.info(
        "Auto-registering cluster '%s' from module %s outputs (remote_host=%s, path=%s)",
        cluster_name,
        module.id,
        remote_host,
        remote_kubeconfig_path,
    )

    return _create_cluster_from_remote(
        db, project, cluster_name, remote_host, remote_kubeconfig_path,
        ssh_credential_id_override=ssh_credential_id_override,
    )


def _read_workspace_file(workspace_path: str | None, rel_path: str) -> str | None:
    """Read a module-surfaced file from inside the persistent workspace.

    Guards against path traversal — the resolved path must stay within the
    workspace root. Returns the file contents, or None if absent/unreadable.
    """
    if not workspace_path or not rel_path:
        return None
    import os

    base = os.path.abspath(workspace_path)
    target = os.path.abspath(os.path.join(base, rel_path))
    if target != base and not target.startswith(base + os.sep):
        logger.warning("Declared cluster.kubeconfig_file '%s' escapes the workspace; ignoring", rel_path)
        return None
    try:
        with open(target) as handle:
            return handle.read()
    except OSError as exc:
        logger.info("Declared cluster.kubeconfig_file not readable (%s): %s", target, exc)
        return None


def _render_input_tokens(value: str, module: ProjectModule) -> str:
    """Expand ``{{inputs.*}}`` tokens exactly like the container engine does for
    step args. ``cluster.kubeconfig_file`` paths routinely embed an input — e.g.
    ocibnkctl writes under a ``{{inputs.poc_name}}/`` directory it creates.
    The path-traversal guard in ``_read_workspace_file`` runs on the rendered
    result, so a malicious input value cannot escape the workspace.
    """
    from services.execution.container_engine import _INPUT_TOKEN_RE

    variables = {**(module.variables or {}), **(module.variable_overrides or {})}

    def _sub(match: re.Match[str]) -> str:
        node: Any = variables
        for part in match.group(1).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return ""
        return "" if node is None else str(node)

    return _INPUT_TOKEN_RE.sub(_sub, value)


def maybe_register_container_cluster(
    db: Session, module: ProjectModule, workspace_path: str | None = None
) -> KubernetesCluster | None:
    """Generic, manifest-driven cluster registration for container-engine modules.

    A container module that produces a cluster declares HOW it surfaces the config
    via a ``cluster`` block in its artifact manifest — bnk-forge stays free of any
    module/cloud/path-specific knowledge::

        "cluster": {
          "name_output": "cluster_name",            # output key for the cluster name
          "name": "{{inputs.poc_name}}",             # fallback name (templated) for tools
                                                     # that write no outputs file
          "api_server_output": "master_url",         # optional output key for the API server
          "region_output": "region",                 # optional output key for the region
          "cloud_provider": "ibm",                   # optional — drives credential-template refresh
          "kubeconfig_file": ".kube/config",         # workspace-relative kubeconfig file, OR
          "kubeconfig_output": "kubeconfig"          # output key holding the kubeconfig
        }

    The kubeconfig is read from the declared workspace file (the module is
    responsible for writing it) or output. The row is linked to its project, so the
    project's cloud credential template keeps any rotating session token current via
    the existing ClusterManagementService.refresh_kubeconfig path (dispatched on
    cloud_provider). Cloud-agnostic. Self-gating and best-effort: returns None unless
    a kubeconfig + cluster name can be resolved; never raises into the caller.
    """
    from services.roks_service import decode_module_kubeconfig_output, kubeconfig_default_context

    outputs: dict[str, Any] = dict(module.outputs or {})
    lib = getattr(module, "library_module", None)
    manifest = getattr(lib, "pack_manifest", None)
    decl = manifest.get("cluster") if isinstance(manifest, dict) else None
    if not isinstance(decl, dict):
        decl = {}

    # Resolve the kubeconfig the module surfaced: declared workspace file first,
    # then a declared/output key.
    kubeconfig_yaml: str | None = None
    kc_file = decl.get("kubeconfig_file")
    if isinstance(kc_file, str) and kc_file:
        kc_file = _render_input_tokens(kc_file, module)
        raw = _read_workspace_file(workspace_path, kc_file)
        if raw and "apiVersion:" in raw:
            kubeconfig_yaml = raw
    if not kubeconfig_yaml:
        kc_out = decl.get("kubeconfig_output")
        if isinstance(kc_out, str) and kc_out:
            kubeconfig_yaml = decode_module_kubeconfig_output({"kubeconfig": outputs.get(kc_out)})
        else:
            kubeconfig_yaml = decode_module_kubeconfig_output(outputs)
    if not kubeconfig_yaml:
        if decl:
            # The manifest promised a cluster but nothing surfaced — say so
            # loudly. Silent skips here cost a live debugging session (#452).
            logger.warning(
                "Module %s declares a cluster block (kubeconfig_file=%r, kubeconfig_output=%r) "
                "but no kubeconfig was surfaced; skipping cluster registration",
                module.id,
                decl.get("kubeconfig_file"),
                decl.get("kubeconfig_output"),
            )
        return None

    name_key = decl.get("name_output") if isinstance(decl.get("name_output"), str) else None
    declared_name = decl.get("name") if isinstance(decl.get("name"), str) else None
    cluster_name = (
        # Outputs are the tool's ground truth and win; the declared (templated)
        # name is the fallback for tools that write no outputs file at all
        # (ocibnkctl surfaces only the workspace kubeconfig, #452).
        (outputs.get(name_key) if name_key else None)
        or outputs.get("cluster_name")
        or outputs.get("openshift_cluster_name")
        or outputs.get("roks_cluster_name")
        or (_render_input_tokens(declared_name, module) if declared_name else None)
    )
    if not cluster_name:
        logger.info("Module %s surfaced a kubeconfig but no cluster_name; skipping registration", module.id)
        return None

    project = module.project
    # cloud_provider drives the credential-template refresh dispatch (ibm/roks →
    # IAM-token refresh). The module declares it (it knows what it provisions);
    # fall back to the project's provider, then "unknown".
    from utils.provider_config import normalize_cloud_provider

    cloud_provider = (
        normalize_cloud_provider(decl.get("cloud_provider"))
        or normalize_cloud_provider(getattr(project, "cloud_provider", None) if project else None)
        or "unknown"
    )
    api_key = decl.get("api_server_output") if isinstance(decl.get("api_server_output"), str) else None
    api_server = (
        (outputs.get(api_key) if api_key else None)
        or outputs.get("master_url")
        or outputs.get("cluster_endpoint")
        or outputs.get("openshift_cluster_public_endpoint")
    )
    region_key = decl.get("region_output") if isinstance(decl.get("region_output"), str) else None
    region = (
        (outputs.get(region_key) if region_key else None)
        or outputs.get("region")
        or (getattr(project, "region", None) if project else None)
    )

    try:
        kubeconfig_yaml = normalize_kubeconfig(kubeconfig_yaml, source=NormalizationSource.CLOUD_API_GENERATED)
    except Exception as exc:
        logger.error("kubeconfig_invariant_violation for cluster %s (module %s): %s", cluster_name, module.id, exc)
        return None

    kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)
    kubeconfig_context = kubeconfig_default_context(kubeconfig_yaml)

    existing = (
        db.query(KubernetesCluster)
        .filter(
            KubernetesCluster.name == cluster_name,
            KubernetesCluster.project_id == module.project_id,
        )
        .first()
    )
    if existing:
        # Ownership check (#79 item 7). The lookup keys on (name, project), but
        # unregister keys on meta_data.source_module_id. Two modules in one
        # project surfacing the same cluster_name used to clobber each other
        # here: B overwrote A's kubeconfig/api_server, and A's destroy-time
        # unregister -- which finds the row by source_module_id -- could then
        # no longer clean up the hijacked row. A re-apply of the SAME module
        # must still refresh its row; a DIFFERENT module must not take it.
        owner = (existing.meta_data or {}).get("source_module_id")
        if owner is not None and owner != module.id:
            logger.warning(
                "Cluster '%s' in project %s is owned by module %s; refusing to "
                "overwrite it from module %s. Give the module a distinct "
                "cluster_name, or destroy the owning module first.",
                cluster_name, module.project_id, owner, module.id,
            )
            return None
        existing.kubeconfig_encrypted = kubeconfig_encrypted
        if kubeconfig_context:
            existing.context = kubeconfig_context
        if api_server:
            existing.api_server = api_server
        existing.status = "active"
        # Deliberately NOT claiming an ownerless row by stamping
        # source_module_id. An ownerless row is hand-registered (or pre-dates
        # ownership tracking); maybe_unregister_container_cluster keys on
        # source_module_id precisely so destroying a module never deletes a
        # hand-registered cluster that shares its name. Adopting here would
        # reintroduce exactly that. Refreshing its kubeconfig, as before, is
        # the right and sufficient behaviour.
        db.flush()
        logger.info("Updated cluster '%s' (id=%s) kubeconfig from module %s", cluster_name, existing.id, module.id)
        return existing

    cluster = KubernetesCluster(
        name=cluster_name,
        context=kubeconfig_context or cluster_name,
        api_server=api_server,
        version=outputs.get("openshift_version") or outputs.get("cluster_version"),
        status="active",
        project_id=module.project_id,
        kubeconfig_encrypted=kubeconfig_encrypted,
        cloud_provider=cloud_provider,
        region=region,
        default_namespace="default",
        meta_data={
            "auto_registered": True,
            "source_module_id": module.id,
            "registered_via": "container_engine_outputs",
        },
    )
    PlatformContextService.apply_cluster_context(cluster)
    db.add(cluster)
    db.flush()
    db.refresh(cluster)
    logger.info("Registered cluster '%s' (id=%s) from module %s outputs", cluster_name, cluster.id, module.id)
    return cluster


def maybe_unregister_container_cluster(db: Session, module: ProjectModule) -> bool:
    """Remove a container-engine-registered cluster when its source module is destroyed.

    Looks up by the ``source_module_id`` meta_data so it never drops a
    hand-registered cluster that happens to share a name.
    """
    # Filter in Python on meta_data so this is DB-agnostic (the Postgres ``->>``
    # JSON operator isn't portable to the SQLite test backend).
    candidates = (
        db.query(KubernetesCluster)
        .filter(KubernetesCluster.project_id == module.project_id)
        .all()
    )
    cluster = next(
        (c for c in candidates if (c.meta_data or {}).get("source_module_id") == module.id),
        None,
    )
    if not cluster:
        return False
    name = cluster.name
    db.delete(cluster)
    db.flush()
    logger.info("Unregistered cluster '%s' (source module %s destroyed)", name, module.id)
    return True


def _fetch_remote_kubeconfig(
    db: Session,
    project: Project,
    remote_host: str,
    remote_kubeconfig_path: str,
    *,
    ssh_credential_id_override: int | None = None,
) -> tuple[str | None, str | None]:
    """
    Fetch kubeconfig from remote host via SSH.

    Args:
        ssh_credential_id_override: If provided, use this credential instead of
            project.ssh_credential_id. Used for bare-metal hosts that have their
            own SSH credential distinct from the project-level one.

    Returns (kubeconfig_yaml, error_message).
    """
    ssh_cred_id = ssh_credential_id_override or project.ssh_credential_id
    if not ssh_cred_id:
        return None, "No SSH credential available (project has none and no override provided)"

    try:
        cred = db.query(SSHCredential).filter(SSHCredential.id == ssh_cred_id).first()
        if not cred:
            return None, f"SSH credential {ssh_cred_id} not found"

        # Use SSH to fetch the kubeconfig file
        import io

        import paramiko

        from core.encryption import decrypt_value
        from models.ssh_credential import SSHCredential as SSHCred

        # Resolve auth
        username = cred.username
        host = cred.host
        port = cred.port or 22

        # Build paramiko key/password kwargs once — used either as the final
        # connect call (no jumphost) or as the tunneled connect over a
        # jumphost transport.
        connect_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 15,
            "look_for_keys": False,
            "allow_agent": False,
        }
        # `sudo_password` is also used by the rung-4 ``sudo -S cat
        # /etc/kubernetes/admin.conf`` fallback in fetch_flattened_kubeconfig_over_ssh.
        # For key-based auth we don't have a sudo password; the ladder will
        # try ``sudo -n`` instead (NOPASSWD only) — see ssh_kubeconfig_fetch.
        sudo_password: str | None = None
        if cred.auth_type == "password" and cred.password_encrypted:
            password = decrypt_value(cred.password_encrypted)
            connect_kwargs["password"] = password
            sudo_password = password
        elif cred.private_key_encrypted:
            key_pem = decrypt_value(cred.private_key_encrypted)
            passphrase = None
            if cred.key_passphrase_encrypted:
                passphrase = decrypt_value(cred.key_passphrase_encrypted)
            pkey = None
            for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                try:
                    key_file = io.StringIO(key_pem)
                    pkey = key_class.from_private_key(key_file, password=passphrase)
                    break
                except (paramiko.SSHException, ValueError):
                    continue
            if not pkey:
                return None, "Failed to load SSH private key"
            connect_kwargs["pkey"] = pkey
        else:
            return None, "No SSH credentials available (no key or password)"

        # Resolve project-level jumphost (the worker often can't reach the
        # bare-metal host's mgmt IP directly; deployments use the project's
        # SSH credential as a jumphost — same as `_build_module_context` in
        # tasks/ssh_tasks.py). Without this tunnel, auto-registration tries
        # a direct connect and gets "Unable to connect to port 22" — the
        # cluster never gets registered, kubeconfig stays unwritten, and
        # downstream OpenTofu modules fail on file("/app/kubeconfig").
        jumphost_client: paramiko.SSHClient | None = None
        if project and project.ssh_credential_id and project.ssh_credential_id != cred.id:
            jh_cred = (
                db.query(SSHCred).filter(SSHCred.id == project.ssh_credential_id).first()
            )
            if jh_cred:
                jh_kwargs: dict[str, Any] = {
                    "hostname": jh_cred.host,
                    "port": jh_cred.port or 22,
                    "username": jh_cred.username,
                    "timeout": 15,
                    "look_for_keys": False,
                    "allow_agent": False,
                }
                if jh_cred.auth_type == "password" and jh_cred.password_encrypted:
                    jh_kwargs["password"] = decrypt_value(jh_cred.password_encrypted)
                elif jh_cred.private_key_encrypted:
                    jh_key_pem = decrypt_value(jh_cred.private_key_encrypted)
                    jh_passphrase = (
                        decrypt_value(jh_cred.key_passphrase_encrypted)
                        if jh_cred.key_passphrase_encrypted else None
                    )
                    jh_pkey = None
                    for kc in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                        try:
                            jh_pkey = kc.from_private_key(
                                io.StringIO(jh_key_pem), password=jh_passphrase,
                            )
                            break
                        except (paramiko.SSHException, ValueError):
                            continue
                    if jh_pkey is None:
                        return None, (
                            f"Failed to load project jumphost SSH key "
                            f"(credential id={jh_cred.id})"
                        )
                    jh_kwargs["pkey"] = jh_pkey
                else:
                    return None, (
                        f"Project jumphost credential id={jh_cred.id} has neither "
                        "password nor private key"
                    )

                jumphost_client = paramiko.SSHClient()
                jumphost_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                jumphost_client.connect(**jh_kwargs)
                jh_transport = jumphost_client.get_transport()
                if jh_transport is None:
                    jumphost_client.close()
                    return None, "Jumphost SSH transport unavailable after connect"
                # Open a direct-tcpip channel from the jumphost to the host
                # and use it as the underlying socket for the host's paramiko
                # client.
                chan = jh_transport.open_channel(
                    "direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=15,
                )
                connect_kwargs["sock"] = chan
                logger.info(
                    "Fetching kubeconfig via jumphost: worker → %s → %s:%d",
                    jh_cred.host, host, port,
                )
            else:
                logger.warning(
                    "Project %s ssh_credential_id=%s not found — attempting direct connect",
                    project.id, project.ssh_credential_id,
                )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(**connect_kwargs)

            # Fetch and flatten using the ladder (oc → kubectl → raw cat → sudo cat)
            # so that OCP/RHEL hosts with file-path refs are inlined at source,
            # AND kubeadm-init'd hosts where the SSH user has no ~/.kube/config
            # still succeed via the rung-4 sudo cat /etc/kubernetes/admin.conf path.
            from services.ssh_kubeconfig_fetch import fetch_flattened_kubeconfig_over_ssh

            try:
                kubeconfig_yaml = fetch_flattened_kubeconfig_over_ssh(
                    client, sudo_password=sudo_password,
                )
            except ValueError as e:
                return None, str(e)

            return kubeconfig_yaml, None

        finally:
            try:
                client.close()
            finally:
                if jumphost_client is not None:
                    try:
                        jumphost_client.close()
                    except Exception:
                        pass

    except Exception as e:
        logger.error("Failed to fetch remote kubeconfig: %s", e)
        return None, str(e)


def _create_cluster_from_remote(
    db: Session,
    project: Project,
    cluster_name: str,
    remote_host: str,
    remote_kubeconfig_path: str,
    *,
    ssh_credential_id_override: int | None = None,
) -> dict[str, Any] | None:
    """Create a new cluster from remote kubeconfig."""
    kubeconfig_yaml, error = _fetch_remote_kubeconfig(
        db, project, remote_host, remote_kubeconfig_path,
        ssh_credential_id_override=ssh_credential_id_override,
    )
    if error:
        logger.error("Failed to fetch kubeconfig for auto-registration: %s", error)
        return {"success": False, "error": error}

    try:
        kc = yaml.safe_load(kubeconfig_yaml)
    except yaml.YAMLError as e:
        logger.error("Failed to parse kubeconfig YAML: %s", e)
        return {"success": False, "error": f"Invalid kubeconfig YAML: {e}"}

    # Extract context and API server
    context = kc.get("current-context", "")
    if not context and kc.get("contexts"):
        context = kc["contexts"][0].get("name", "")

    api_server = None
    ctx_cluster_name = None
    for ctx_entry in kc.get("contexts", []):
        if ctx_entry.get("name") == context:
            ctx_cluster_name = ctx_entry.get("context", {}).get("cluster")
            break

    for cl_entry in kc.get("clusters", []):
        if ctx_cluster_name and cl_entry.get("name") == ctx_cluster_name:
            api_server = cl_entry.get("cluster", {}).get("server")
            break

    if not api_server and kc.get("clusters"):
        api_server = kc["clusters"][0].get("cluster", {}).get("server")

    # For localhost-bound kubeconfigs (like kind), derive SSH tunnel target
    ssh_remote_host = remote_host
    ssh_remote_port = 6443

    if api_server:
        from urllib.parse import urlparse

        parsed = urlparse(api_server)
        if parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0"):
            # This is a localhost-bound cluster (like kind)
            # Tunnel target should be localhost on the remote host
            ssh_remote_host = parsed.hostname
            if parsed.port:
                ssh_remote_port = parsed.port
            logger.info(
                "Detected localhost-bound cluster API server %s, setting tunnel target to %s:%s",
                api_server,
                ssh_remote_host,
                ssh_remote_port,
            )

    # Paranoia check: SSH remote-flatten should have produced portable output.
    kubeconfig_yaml = normalize_kubeconfig(
        kubeconfig_yaml, source=NormalizationSource.SSH_DISCOVERY
    )

    kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)

    cluster = KubernetesCluster(
        project_id=project.id,
        name=cluster_name,
        context=context,
        api_server=api_server,
        kubeconfig_encrypted=kubeconfig_encrypted,
        cloud_provider="on-prem",
        default_namespace="default",
        ssh_tunnel_enabled=True,
        ssh_remote_k8s_host=ssh_remote_host,
        ssh_remote_k8s_port=ssh_remote_port,
        ssh_credential_id=ssh_credential_id_override or project.ssh_credential_id,
        status="active",
    )

    PlatformContextService.apply_cluster_context(cluster)

    db.add(cluster)
    db.flush()
    db.refresh(cluster)

    logger.info(
        "Auto-registered cluster '%s' (id=%s) with SSH tunnel to %s:%s",
        cluster_name,
        cluster.id,
        ssh_remote_host,
        ssh_remote_port,
    )

    return {
        "success": True,
        "action": "created",
        "cluster_id": cluster.id,
        "cluster_name": cluster.name,
        "api_server": cluster.api_server,
        "ssh_tunnel_enabled": cluster.ssh_tunnel_enabled,
        "ssh_remote_k8s_host": cluster.ssh_remote_k8s_host,
        "ssh_remote_k8s_port": cluster.ssh_remote_k8s_port,
    }


def _update_cluster_from_remote(
    db: Session,
    cluster: KubernetesCluster,
    project: Project,
    remote_host: str,
    remote_kubeconfig_path: str,
    *,
    ssh_credential_id_override: int | None = None,
) -> dict[str, Any] | None:
    """Update an existing cluster's kubeconfig from remote."""
    kubeconfig_yaml, error = _fetch_remote_kubeconfig(
        db, project, remote_host, remote_kubeconfig_path,
        ssh_credential_id_override=ssh_credential_id_override,
    )
    if error:
        logger.error("Failed to fetch kubeconfig for cluster update: %s", error)
        return {"success": False, "error": error}

    try:
        kc = yaml.safe_load(kubeconfig_yaml)
    except yaml.YAMLError as e:
        logger.error("Failed to parse kubeconfig YAML: %s", e)
        return {"success": False, "error": f"Invalid kubeconfig YAML: {e}"}

    # Update context and API server
    context = kc.get("current-context", "")
    if not context and kc.get("contexts"):
        context = kc["contexts"][0].get("name", "")

    api_server = None
    ctx_cluster_name = None
    for ctx_entry in kc.get("contexts", []):
        if ctx_entry.get("name") == context:
            ctx_cluster_name = ctx_entry.get("context", {}).get("cluster")
            break

    for cl_entry in kc.get("clusters", []):
        if ctx_cluster_name and cl_entry.get("name") == ctx_cluster_name:
            api_server = cl_entry.get("cluster", {}).get("server")
            break

    if not api_server and kc.get("clusters"):
        api_server = kc["clusters"][0].get("cluster", {}).get("server")

    # Update tunnel target for localhost-bound clusters
    if api_server:
        from urllib.parse import urlparse

        parsed = urlparse(api_server)
        if parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0"):
            cluster.ssh_remote_k8s_host = parsed.hostname
            if parsed.port:
                cluster.ssh_remote_k8s_port = parsed.port

    # Paranoia check: SSH remote-flatten should have produced portable output.
    kubeconfig_yaml = normalize_kubeconfig(
        kubeconfig_yaml, source=NormalizationSource.SSH_DISCOVERY
    )

    cluster.context = context
    cluster.api_server = api_server
    cluster.kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)
    cluster.ssh_tunnel_enabled = True
    cluster.ssh_credential_id = cluster.ssh_credential_id or project.ssh_credential_id

    PlatformContextService.apply_cluster_context(cluster)

    db.flush()
    db.refresh(cluster)

    logger.info(
        "Updated cluster '%s' (id=%s) kubeconfig from remote, tunnel target %s:%s",
        cluster.name,
        cluster.id,
        cluster.ssh_remote_k8s_host,
        cluster.ssh_remote_k8s_port,
    )

    return {
        "success": True,
        "action": "updated",
        "cluster_id": cluster.id,
        "cluster_name": cluster.name,
        "api_server": cluster.api_server,
        "ssh_tunnel_enabled": cluster.ssh_tunnel_enabled,
        "ssh_remote_k8s_host": cluster.ssh_remote_k8s_host,
        "ssh_remote_k8s_port": cluster.ssh_remote_k8s_port,
    }
