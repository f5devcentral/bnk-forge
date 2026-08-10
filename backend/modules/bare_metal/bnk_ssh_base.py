"""
bnk_ssh_base — shared base for SSH ports of the BNK-layer catalog modules (ADR-204).

The catalog modules 18–25 install K8s resources / Helm charts by reaching the
cluster API from the backend (kubernetes-direct / operator / OpenTofu). On
constrained bare-metal DPU servers that path is fragile (no routable cluster API,
no operator WS, flaky tunnel). These SSH ports run the *same* operations as
on-host ``sudo kubectl`` / ``sudo helm`` commands over the existing SSHEngine —
the transport modules 1–17 already use against the host's local admin kubeconfig.

Design:
  - Rendering is PURE and SSH-free: ``render_manifests()`` / ``render_helm_values()``
    produce the exact dicts that get applied. This is what the ADR-204 parity gate
    diffs against the catalog path's ``build_manifest_payload`` /
    ``build_helm_payload`` output for the DPU case (see tests/.../test_*_parity.py).
  - ``execute()`` applies the rendered output over a live SSH session using
    ``sudo kubectl apply -f -`` (manifests) or ``sudo helm upgrade --install``
    (helm), then waits for readiness and collects outputs.
  - ``destroy()`` reverses: ``kubectl delete`` / ``helm uninstall`` (S6).

Variable rendering reuses the d019 transforms (``MODULE_TRANSFORMS``) via path
aliases registered in ``blueprint_context.py`` — the transforms are engine-agnostic.
"""

from __future__ import annotations

import shlex
import time
from typing import Any

import yaml

from modules.base import SSHModule


def manifests_to_yaml(manifests: list[dict[str, Any]]) -> str:
    """Serialize a list of manifest dicts to a multi-document YAML string.

    Key order is preserved (sort_keys=False) so rendered output is stable and
    diff-friendly for parity assertions.
    """
    return "\n---\n".join(
        yaml.safe_dump(m, default_flow_style=False, sort_keys=False) for m in manifests
    )


class BnkSSHModule(SSHModule):
    """Base for ``bare-metal/bnk-*`` SSH ports of the BNK layer.

    Subclasses implement EITHER ``render_manifests()`` (manifest modules) OR set
    the helm class attributes + implement ``render_helm_values()`` (helm modules).
    """

    category = "bare-metal"
    phase = "BNK Platform"
    target = "host"

    # On-host CLI prefixes. Modules 1–17 already run `sudo kubectl` against the
    # host admin kubeconfig (/root/.kube/config after kubeadm-init); helm reads
    # the same kubeconfig under sudo.
    KUBECTL = "sudo kubectl"
    HELM = "sudo helm"

    # ── Helm configuration (manifest modules leave chart_ref empty) ──────────
    chart_ref: str = ""               # e.g. "oci://repo.f5.com/charts/f5-lifecycle-operator"
    release_name: str = ""
    release_name_var: str = "release_name"
    chart_version: str = ""           # static fallback chart version
    chart_version_var: str = ""       # variable name holding the chart version
    oci_registry: str = ""            # e.g. "repo.f5.com" — non-empty triggers registry login
    create_namespace: bool = True

    # Namespace resolution: the variable name to read the target namespace from,
    # plus a fallback. DPU transforms force these to f5-bnk where appropriate.
    namespace_var: str = "namespace"
    default_namespace: str = "default"

    @property
    def is_helm(self) -> bool:
        return bool(self.chart_ref)

    # ── Pure render contract (no SSH — used by execute() AND parity tests) ───

    def render_manifests(self, variables: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the K8s manifest dicts this module applies. Override in manifest modules."""
        return []

    def render_helm_values(self, variables: dict[str, Any]) -> dict[str, Any]:
        """Return the Helm values dict. Override in helm modules."""
        return {}

    def resolve_namespace(self, variables: dict[str, Any]) -> str:
        ns = variables.get(self.namespace_var)
        if isinstance(ns, str) and ns.strip():
            return ns.strip()
        return self.default_namespace

    def resolve_release_name(self, variables: dict[str, Any]) -> str:
        rn = variables.get(self.release_name_var)
        if isinstance(rn, str) and rn.strip():
            return rn.strip()
        return self.release_name

    def resolve_chart_version(self, variables: dict[str, Any]) -> str:
        if self.chart_version_var and variables.get(self.chart_version_var):
            return str(variables[self.chart_version_var]).strip()
        return self.chart_version

    def get_readiness_waits(self, variables: dict[str, Any]) -> list[dict[str, Any]]:
        """Return kubectl-wait specs to run after apply.

        Each spec: {"kind": str, "name": str|"--all", "namespace": str|None,
                    "condition": str (e.g. "condition=Established"),
                    "timeout": int (seconds, optional)}.
        """
        return []

    def get_required_crds(self, variables: dict[str, Any]) -> list[str]:
        """CRD names (``plural.group``) that must be Established before this module
        applies its manifests.

        FLO installs some CRDs (e.g. ``F5SPKVlan``) asynchronously while it
        reconciles the CNEInstance, so a module that creates those CRs races the
        CRD's registration and fails with "no matches for kind". Declaring the
        CRD here makes ``execute()`` gate the apply on it. Override in subclasses;
        default is none.
        """
        return []

    def get_required_deployments(self, variables: dict[str, Any]) -> list[dict[str, str]]:
        """Deployments (``{"name": str, "namespace": str}``) that must be
        ``Available`` before this module applies its manifests.

        A CRD being Established guarantees the *type* is served, but not that an
        admission webhook backed by a Deployment is up. F5SPKVlan apply is gated
        by the ``f5validate.f5net.com`` validating webhook served by
        ``f5-cne-controller`` on :3340; on a cold deploy that Pod becomes Ready
        minutes after the CRD is Established, so the apply otherwise fails with
        "failed calling webhook ... connect: connection refused". Declaring the
        backing Deployment here gates the apply on it (see
        ``_wait_for_required_deployments``). Override in subclasses; default none.
        """
        return []

    def collect_outputs(self, session: Any, variables: dict[str, Any]) -> dict[str, Any]:
        """Collect module outputs after apply. Default: static outputs only."""
        return self.get_static_outputs(variables)

    # ── Engine entrypoint ────────────────────────────────────────────────────

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        t0 = time.monotonic()
        tag = f"[{self.path.split('/')[-1]}]"

        if self.is_helm:
            self._helm_registry_login(session, variables, on_output)
            self._helm_upgrade_install(session, variables, on_output)
        else:
            manifests = self.render_manifests(variables)
            if not manifests:
                raise RuntimeError(f"{tag} render_manifests() returned no documents")
            self._wait_for_required_crds(session, variables, on_output)
            self._wait_for_required_deployments(session, variables, on_output)
            on_output(f"{tag} Applying {len(manifests)} manifest document(s)...")
            self._apply_manifests(session, manifests, on_output)

        for spec in self.get_readiness_waits(variables):
            self._kubectl_wait(session, spec, on_output)

        outputs = self.collect_outputs(session, variables) or {}
        outputs["execution_duration_seconds"] = round(time.monotonic() - t0, 1)
        on_output(f"{tag} Complete ({outputs['execution_duration_seconds']}s)")
        return outputs

    # ── kubectl helpers ───────────────────────────────────────────────────────

    def _wait_for_required_crds(
        self, session: Any, variables: dict[str, Any], on_output: Any,
    ) -> None:
        """Block until each ``get_required_crds()`` entry is Established, then drop
        kubectl's discovery cache so the subsequent apply can resolve the kind.

        FLO serves CRDs like ``F5SPKVlan`` asynchronously after the CNEInstance
        reconciles; without this gate, the apply races the CRD and reports
        "no matches for kind ... ensure CRDs are installed first". kubectl also
        caches API discovery for ~10m, so a discovery that ran before the CRD was
        served would keep failing the apply — hence the cache drop after the wait.
        """
        crds = self.get_required_crds(variables)
        if not crds:
            return
        tag = f"[{self.path.split('/')[-1]}]"
        for crd in crds:
            on_output(f"{tag} Waiting for CRD {crd} to exist + be Established...")
            crd_q = shlex.quote(crd)
            # ``kubectl wait`` errors immediately ("NotFound") on a resource that
            # doesn't exist yet — it only waits on the *condition*, not creation.
            # FLO installs this CRD asynchronously moments after the CNEInstance
            # reconciles, so poll for the CRD to APPEAR first (up to ~5 min),
            # then wait for it to be Established.
            cmd = (
                f"for _i in $(seq 1 60); do "
                f"{self.KUBECTL} get crd {crd_q} >/dev/null 2>&1 && break; "
                f"sleep 5; done; "
                f"{self.KUBECTL} wait --for=condition=established crd/{crd_q} --timeout=120s"
            )
            r = session.execute(cmd, timeout=480)
            if r.exit_code != 0:
                raise RuntimeError(
                    f"CRD {crd} not present/Established within timeout: {r.stderr[:300]}"
                )
        session.execute(
            "sudo rm -rf /root/.kube/cache/discovery ~/.kube/cache/discovery", timeout=15,
        )

    def _wait_for_required_deployments(
        self, session: Any, variables: dict[str, Any], on_output: Any,
    ) -> None:
        """Block until each ``get_required_deployments()`` entry is Available.

        Some CRs are gated by an admission webhook whose backend is a Deployment
        (F5SPKVlan -> ``f5validate.f5net.com``, served by ``f5-cne-controller``).
        On a cold from-scratch deploy the CRD is Established minutes before that
        Pod accepts connections on the webhook port, so the apply fails with
        "failed calling webhook ... connect: connection refused". Waiting for the
        backing Deployment to be Available closes that window — the CRD gate only
        guarantees the *type* is served, not that the webhook backend is up.

        Like the CRD gate, the Deployment may not exist yet (FLO creates it while
        reconciling the CNEInstance), so poll for it to APPEAR first, then wait on
        the Available condition.
        """
        deployments = self.get_required_deployments(variables)
        if not deployments:
            return
        tag = f"[{self.path.split('/')[-1]}]"
        for dep in deployments:
            name = dep["name"]
            ns = dep.get("namespace")
            name_q = shlex.quote(name)
            ns_flag = f"-n {shlex.quote(ns)} " if ns else ""
            on_output(f"{tag} Waiting for deployment {name} to be Available (webhook backend)...")
            cmd = (
                f"for _i in $(seq 1 60); do "
                f"{self.KUBECTL} get deploy {name_q} {ns_flag}>/dev/null 2>&1 && break; "
                f"sleep 5; done; "
                f"{self.KUBECTL} wait --for=condition=Available deploy/{name_q} {ns_flag}--timeout=300s"
            )
            r = session.execute(cmd, timeout=660)
            if r.exit_code != 0:
                raise RuntimeError(
                    f"Deployment {name} not Available within timeout: {r.stderr[:300]}"
                )

    # Transient admission-webhook transport failure: the API server could not
    # *reach* the webhook backend (Pod still cold-starting), distinct from an
    # admission *denial* ("admission webhook ... denied the request"). Safe to
    # retry since ``kubectl apply`` is idempotent. Belt to the deployment gate's
    # braces — closes the sub-second gap between Pod-Ready and endpoint routing.
    WEBHOOK_RETRY_MARKER = "failed calling webhook"
    WEBHOOK_RETRY_ATTEMPTS = 4
    WEBHOOK_RETRY_SLEEP = 15

    @staticmethod
    def _write_remote_tmp(session: Any, content: str) -> str:
        """mktemp a private 0600 file and write ``content`` via a non-sudo heredoc.

        Returns the remote path. The apply/helm step then reads the file
        (``-f <path>``) instead of piping a heredoc straight into ``sudo``, which
        matters because ``SSHSession`` feeds the sudo password to ``sudo -S`` via
        the channel stdin: ``sudo kubectl apply -f - << EOF`` would bind sudo's
        stdin to the heredoc, so on password-auth hosts sudo eats the manifest as
        its password and the apply fails. ``mktemp`` also gives an unpredictable
        path (CWE-377) unlike a fixed name.

        Note this write command itself carries no ``sudo`` token, so ``SSHSession``
        does not rewrite it — UNLESS ``content`` literally contains ``"sudo "``, in
        which case the pre-existing blind ``sudo ``→``sudo -S `` replace in
        ``_inject_sudo_stdin_flag`` would still corrupt the heredoc body. None of
        the BNK manifests/values/creds contain that string; a full fix belongs in
        that helper, not here.
        """
        mk = session.execute("umask 077 && mktemp /tmp/bnk.XXXXXXXX", timeout=15)
        remote = (mk.stdout or "").strip()
        if mk.exit_code != 0 or not remote:
            raise RuntimeError(
                f"mktemp failed on remote host (exit {mk.exit_code}): {mk.stderr[:200]}"
            )
        session.execute(
            f"cat > {shlex.quote(remote)} << 'BNK_TMP_EOF'\n{content}\nBNK_TMP_EOF",
            timeout=30,
        )
        return remote

    @staticmethod
    def _shred_remote_tmp(session: Any, remote: str) -> None:
        session.execute(
            f"shred -u {shlex.quote(remote)} 2>/dev/null || rm -f {shlex.quote(remote)}",
            timeout=15,
        )

    def _apply_manifests(
        self, session: Any, manifests: list[dict[str, Any]], on_output: Any,
    ) -> None:
        """Apply manifests via ``sudo kubectl apply -f <file>``.

        Manifests are expected to be self-contained (namespaced resources carry
        their own metadata.namespace), matching the catalog render. Retries on a
        transient admission-webhook transport error (see ``WEBHOOK_RETRY_MARKER``).
        The manifest is written to a private temp file first — see
        ``_write_remote_tmp`` for why we don't pipe a heredoc into ``sudo``.
        """
        docs = manifests_to_yaml(manifests)
        tag = f"[{self.path.split('/')[-1]}]"
        remote = self._write_remote_tmp(session, docs)
        cmd = f"{self.KUBECTL} apply -f {shlex.quote(remote)}"
        r = None
        try:
            for attempt in range(1, self.WEBHOOK_RETRY_ATTEMPTS + 1):
                r = session.execute(cmd, timeout=self.timeout)
                if r.exit_code == 0:
                    break
                if self.WEBHOOK_RETRY_MARKER in r.stderr and attempt < self.WEBHOOK_RETRY_ATTEMPTS:
                    on_output(
                        f"{tag} admission webhook not reachable yet "
                        f"(attempt {attempt}/{self.WEBHOOK_RETRY_ATTEMPTS}); "
                        f"retrying in {self.WEBHOOK_RETRY_SLEEP}s..."
                    )
                    time.sleep(self.WEBHOOK_RETRY_SLEEP)
                    continue
                raise RuntimeError(f"kubectl apply failed (exit {r.exit_code}): {r.stderr[:500]}")
        finally:
            self._shred_remote_tmp(session, remote)
        for line in r.stdout.strip().splitlines():
            if line.strip():
                on_output(f"  {line}")

    def _kubectl_wait(self, session: Any, spec: dict[str, Any], on_output: Any) -> None:
        kind = spec["kind"]
        name = spec.get("name", "--all")
        condition = spec["condition"]
        timeout = int(spec.get("timeout", 120))
        ns = spec.get("namespace")
        ns_flag = f"-n {shlex.quote(ns)} " if ns else ""
        target = "--all" if name == "--all" else shlex.quote(name)
        cmd = (
            f"{self.KUBECTL} wait {shlex.quote(kind)} {target} {ns_flag}"
            f"--for={shlex.quote(condition)} --timeout={timeout}s"
        )
        on_output(f"[{self.path.split('/')[-1]}] Waiting: {kind}/{name} --for={condition}")
        r = session.execute(cmd, timeout=timeout + 30)
        if r.exit_code != 0:
            raise RuntimeError(
                f"Readiness wait failed for {kind}/{name} ({condition}): {r.stderr[:300]}"
            )

    def jsonpath_get(
        self, session: Any, kind: str, name: str, namespace: str | None, jsonpath: str,
    ) -> str | None:
        """Read a single field from a resource via ``kubectl get -o jsonpath``."""
        ns_flag = f"-n {shlex.quote(namespace)} " if namespace else ""
        cmd = (
            f"{self.KUBECTL} get {shlex.quote(kind)} {shlex.quote(name)} {ns_flag}"
            f"-o jsonpath={shlex.quote('{' + jsonpath + '}')} 2>/dev/null"
        )
        r = session.execute(cmd, timeout=30)
        if r.exit_code != 0:
            return None
        out = r.stdout.strip()
        return out or None

    def resource_exists(
        self, session: Any, kind: str, name: str, namespace: str | None,
    ) -> bool:
        ns_flag = f"-n {shlex.quote(namespace)} " if namespace else ""
        cmd = f"{self.KUBECTL} get {shlex.quote(kind)} {shlex.quote(name)} {ns_flag}>/dev/null 2>&1"
        return session.execute(cmd, timeout=30).exit_code == 0

    # ── helm helpers (port of poc-deployer 51-install-flo.sh) ─────────────────

    def _helm_registry_login(self, session: Any, variables: dict[str, Any], on_output: Any) -> None:
        """``helm registry login`` to an OCI registry using FAR credentials.

        Handles both cne_pull_secret formats (bare base64 SA key / pre-built
        dockerconfigjson), mirroring k8s/bnk-prerequisites download-manifest.sh.
        The credential is written 0600, used, then shredded — never logged.
        """
        if not self.oci_registry:
            return
        secret = variables.get("cne_pull_secret")
        if not secret or not str(secret).strip():
            raise RuntimeError(
                f"cne_pull_secret is required for helm registry login to {self.oci_registry}"
            )
        on_output(f"[{self.path.split('/')[-1]}] helm registry login {self.oci_registry}...")
        # mktemp (unpredictable path, 0600) + shred in finally so the credential
        # never lingers on a helm-registry-login failure.
        remote = self._write_remote_tmp(session, secret)
        rq = shlex.quote(remote)
        login = (
            f"set -o pipefail; RAW=$(cat {rq}); "
            f'DEC=$(printf "%s" "$RAW" | base64 -d 2>/dev/null || true); '
            f'if printf "%s" "$DEC" | grep -q \'"auths"\'; then '
            f"  AUTH=$(printf \"%s\" \"$DEC\" | python3 -c \""
            f"import sys,json,base64;d=json.load(sys.stdin);"
            f"a=d['auths']['{self.oci_registry}']['auth'];"
            f"print(base64.b64decode(a).decode())\"); "
            f'  USER=${{AUTH%%:*}}; PASS=${{AUTH#*:}}; '
            f'  printf "%s" "$PASS" | {self.HELM} registry login {self.oci_registry} -u "$USER" --password-stdin; '
            f"else "
            f'  printf "%s" "$RAW" | {self.HELM} registry login {self.oci_registry} -u _json_key_base64 --password-stdin; '
            f"fi"
        )
        try:
            r = session.execute(login, timeout=60)
            if r.exit_code != 0:
                raise RuntimeError(
                    f"helm registry login to {self.oci_registry} failed (exit {r.exit_code}): {r.stderr[:300]}"
                )
        finally:
            self._shred_remote_tmp(session, remote)

    def _helm_upgrade_install(self, session: Any, variables: dict[str, Any], on_output: Any) -> None:
        ns = self.resolve_namespace(variables)
        release = self.resolve_release_name(variables)
        version = self.resolve_chart_version(variables)

        values = self.render_helm_values(variables) or {}
        values_yaml = yaml.safe_dump(values, default_flow_style=False, sort_keys=False)
        # The values file holds the license JWT — mktemp (unpredictable path) + shred
        # in finally so it never lingers on a helm timeout.
        remote = self._write_remote_tmp(session, values_yaml)

        ver_flag = f"--version {shlex.quote(version)} " if version else ""
        cns_flag = "--create-namespace " if self.create_namespace else ""
        cmd = (
            f"{self.HELM} upgrade --install {shlex.quote(release)} {shlex.quote(self.chart_ref)} "
            f"{ver_flag}-f {shlex.quote(remote)} -n {shlex.quote(ns)} {cns_flag}"
            f"--wait --timeout {self.timeout}s"
        )
        on_output(
            f"[{self.path.split('/')[-1]}] helm upgrade --install {release} "
            f"{self.chart_ref}{' ' + version if version else ''} -n {ns}"
        )
        try:
            r = session.execute(cmd, timeout=self.timeout + 120)
            if r.exit_code != 0:
                raise RuntimeError(
                    f"helm upgrade --install {self.release_name} failed (exit {r.exit_code}): {r.stderr[:500]}"
                )
        finally:
            self._shred_remote_tmp(session, remote)
        for line in r.stdout.strip().splitlines():
            if line.strip():
                on_output(f"  {line}")

    # ── destroy (S6 wires this into the engine) ───────────────────────────────

    def destroy(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Reverse apply: helm uninstall, or kubectl delete in reverse order.

        Raises on a genuine teardown failure so the engine reports ``success=False``
        instead of silently marking the module destroyed while resources live on.
        ``--ignore-not-found`` keeps an already-gone resource/release a success
        (idempotent re-runs), but a real error (API unreachable, RBAC, timeout,
        stuck finalizer) now surfaces.
        """
        tag = f"[{self.path.split('/')[-1]}]"
        if self.is_helm:
            ns = self.resolve_namespace(variables)
            release = self.resolve_release_name(variables)
            on_output(f"{tag} helm uninstall {release} -n {ns}")
            r = session.execute(
                f"{self.HELM} uninstall {shlex.quote(release)} -n {shlex.quote(ns)} "
                f"--ignore-not-found",
                timeout=self.timeout,
            )
            if r.exit_code != 0:
                raise RuntimeError(
                    f"{tag} helm uninstall failed (exit {r.exit_code}): {r.stderr[:300]}"
                )
        else:
            manifests = self.render_manifests(variables)
            docs = manifests_to_yaml(list(reversed(manifests)))
            on_output(f"{tag} kubectl delete {len(manifests)} document(s)")
            remote = self._write_remote_tmp(session, docs)
            try:
                r = session.execute(
                    f"{self.KUBECTL} delete --ignore-not-found=true -f {shlex.quote(remote)}",
                    timeout=self.timeout,
                )
                if r.exit_code != 0:
                    raise RuntimeError(
                        f"{tag} kubectl delete failed (exit {r.exit_code}): {r.stderr[:300]}"
                    )
            finally:
                self._shred_remote_tmp(session, remote)
        return {"destroyed": True}

    # ── prereq: helm presence for helm modules ────────────────────────────────

    def prereq_commands(self, variables: dict[str, Any]) -> list[str]:
        if self.is_helm:
            return ["command -v helm >/dev/null 2>&1 || { echo 'helm not found on host'; exit 1; }"]
        return []
