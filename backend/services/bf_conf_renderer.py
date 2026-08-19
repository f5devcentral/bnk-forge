"""bf.conf template renderer.

The seeded templates expect an Ansible-style context:

    {{ bfb_hostname }}                                  — DPU hostname (uses serial#)
    {{ dpu_mtu }}                                       — uplink MTU
    {{ DPU_UBUNTU_PASSWORD_HASH }}                      — SHA-512 crypt hash
    {{ hostvars[bfb_hostname].dns_ip }}                 — comma-separated DNS servers
    {{ hostvars[bfb_hostname].dns_domains }}            — search domain
    {{ hostvars[bfb_hostname].oob_net0_ip }}            — oob_net0 address/CIDR (LAG template only)
    {{ hostvars[bfb_hostname].default_gateway }}        — default via (LAG template)
    {{ hostvars[bfb_hostname].authorized_keys }}        — pubkey for root (multi-line)
    {{ hostvars[bfb_hostname].vlans }}                  — list of {name, tag, ip, uplink, default_gateway}
    {{ hostvars[bfb_hostname].tmfifo_dpu_ip }}          — DPU-side tmfifo_net0 address/CIDR
                                                          (e.g. 192.168.100.2/30 for rshim0,
                                                           192.168.101.2/30 for rshim1, …)

Rendering is strict: unknown variables raise so a mis-configured template
is caught at flash time rather than producing broken netplan on the DPU.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

from jinja2 import Environment, StrictUndefined, TemplateError

from models.dpu import BfConfTemplate, Dpu, ProjectDpuSettings
from models.ssh_credential import SSHCredential

logger = logging.getLogger(__name__)


@dataclass
class VlanContext:
    name: str           # netplan interface name (e.g. "vlan100")
    tag: int            # 802.1Q VLAN id
    ip: str             # CIDR (e.g. "10.10.20.5/24")
    uplink: str         # bond0 | p0 | p1
    default_gateway: str | None = None


@dataclass
class HostContext:
    oob_net0_ip: str | None = None
    default_gateway: str | None = None
    dns_ip: str = ""
    dns_domains: str = ""
    ntp_servers: str = ""
    authorized_keys: str = ""
    vlans: list[VlanContext] = field(default_factory=list)
    # Per-DPU tmfifo_net0 address (CIDR). The /30 host side runs on
    # 192.168.{100+rshim_index}.1; the DPU-side gets .2 in the same /30 so
    # multi-DPU hosts don't collide on the legacy fixed 192.168.100.2.
    tmfifo_dpu_ip: str = "192.168.100.2/30"


@dataclass
class RenderContext:
    bfb_hostname: str
    dpu_mtu: int
    ubuntu_password_hash: str
    host: HostContext

    def to_jinja(self) -> dict:
        # The seeded templates use `hostvars[bfb_hostname].*` — mirror Ansible's
        # shape with a dict keyed by hostname.
        host_dict = {
            "oob_net0_ip": self.host.oob_net0_ip,
            "default_gateway": self.host.default_gateway,
            "dns_ip": self.host.dns_ip,
            "dns_domains": self.host.dns_domains,
            "ntp_servers": self.host.ntp_servers,
            "authorized_keys": self.host.authorized_keys,
            "tmfifo_dpu_ip": self.host.tmfifo_dpu_ip,
            "vlans": [
                {
                    "name": v.name,
                    "tag": v.tag,
                    "ip": v.ip,
                    "uplink": v.uplink,
                    **({"default_gateway": v.default_gateway} if v.default_gateway else {}),
                }
                for v in self.host.vlans
            ],
        }
        return {
            "bfb_hostname": self.bfb_hostname,
            "dpu_mtu": self.dpu_mtu,
            "DPU_UBUNTU_PASSWORD_HASH": self.ubuntu_password_hash,
            "hostvars": {self.bfb_hostname: host_dict},
        }


class BfConfRenderError(Exception):
    """Raised when a template can't be rendered (bad vars, syntax, etc)."""


def render_bf_conf(template: BfConfTemplate, context: RenderContext) -> str:
    """Render the Jinja2 template into a concrete bf.conf string."""
    env = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    try:
        jt = env.from_string(template.content)
        return jt.render(**context.to_jinja())
    except TemplateError as exc:
        raise BfConfRenderError(
            f"Failed to render bf.conf template '{template.name}': {exc}"
        ) from exc


def sha512_crypt(plaintext: str) -> str:
    """Return an /etc/shadow-compatible SHA-512 crypt hash of `plaintext`.

    Equivalent to `openssl passwd -6 '<plaintext>'`. Uses passlib's
    portable implementation — the stdlib `crypt` module is deprecated in
    Python 3.13 and produces malformed output on macOS.
    """
    from passlib.hash import sha512_crypt as _sha512
    return _sha512.hash(plaintext)


def public_key_from_private(private_key_pem: str, comment: str | None = None) -> str:
    """Derive the OpenSSH-format public key from a private key (PEM or OpenSSH).

    Supports Ed25519 and RSA — the two formats our keygen endpoint produces.
    """
    from cryptography.hazmat.primitives import serialization

    data = private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem
    key = serialization.load_ssh_private_key(data, password=None)
    pub = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode()
    if comment:
        pub = f"{pub} {comment}"
    return pub


# ── Context builder ────────────────────────────────────────────────────────

def _rshim_index(rshim_device: str | None) -> int:
    """Return the integer index of /dev/rshimN, or 0 on parse failure."""
    name = (rshim_device or "rshim0").strip()
    digits = name[len("rshim"):] if name.startswith("rshim") else name
    try:
        idx = int(digits)
    except ValueError:
        return 0
    if idx < 0 or idx > 100:  # sanity clamp; rshim indexes are tiny
        return 0
    return idx


def derive_tmfifo_dpu_ip(rshim_device: str | None, dpu: Dpu | None = None) -> str:
    """Compute the DPU-side tmfifo_net0 address (CIDR) for a given rshimN or persisted Dpu.

    If dpu.dpu_tmfifo_ip is persisted (from cluster-scoped tmfifo IPAM) AND the
    DPU is still a cluster member (kubernetes_cluster_id is not None), uses it.
    Otherwise falls back to host-local formula:
        rshim0 → 192.168.100.2/30
        rshim1 → 192.168.101.2/30

    The cluster_id guard is the belt-and-braces half of A (ADR-424 cold audit):
    after cluster-delete, ondelete=SET NULL clears kubernetes_cluster_id but
    a stale dpu_tmfifo_ip can survive. Checking cluster membership here ensures
    a re-flash always bakes the correct local /30 rather than the orphaned one.
    """
    if dpu is not None:
        dpu_ip = getattr(dpu, "dpu_tmfifo_ip", None)
        # Only trust the persisted IP when the DPU is still a cluster member.
        if dpu_ip and getattr(dpu, "kubernetes_cluster_id", None) is not None:
            return f"{dpu_ip}/30"
    return f"192.168.{100 + _rshim_index(rshim_device)}.2/30"


def derive_tmfifo_dpu_host(rshim_device: str | None, dpu: Dpu | None = None) -> str:
    """Bare DPU-side tmfifo IP (no /CIDR), suitable for SSH targets."""
    if dpu is not None:
        dpu_ip = getattr(dpu, "dpu_tmfifo_ip", None)
        if dpu_ip and getattr(dpu, "kubernetes_cluster_id", None) is not None:
            return dpu_ip
    return f"192.168.{100 + _rshim_index(rshim_device)}.2"


def _multi_dpu_hostname_suffix(dpu: Dpu) -> str:
    """Suffix for one DPU on a multi-DPU host. 1-indexed from rshim.

    The host hosts more than one in-band DPU, so every DPU needs a
    distinct hostname. We number them 1, 2, 3, … in rshim-index order:

        rshim0 → "1"   (renders <host>-dpu1)
        rshim1 → "2"   (renders <host>-dpu2)

    1-indexed (not 0-indexed) so the names read naturally to humans —
    "DPU 1" / "DPU 2" rather than "DPU 0" / "DPU 1".

    Pre-probe (rshim_device null) fallback: a sanitised PCI BDF, with
    full domain prefix so DPUs that share a short BDF but sit in
    different PCI domains stay unique.
    """
    import re as _re

    rshim = getattr(dpu, "rshim_device", None)
    if rshim and rshim.startswith("rshim"):
        idx = rshim[len("rshim"):]
        if idx.isdigit():
            return str(int(idx) + 1)
    pci = getattr(dpu, "pci_address", None)
    if pci:
        cleaned = _re.sub(r"[^A-Za-z0-9]+", "-", pci).strip("-").lower()
        if cleaned:
            return f"-{cleaned}"
    return str(dpu.id)


def _derive_bfb_hostname(dpu: Dpu, *, peer_count: int = 1) -> str:
    """Return the hostname to bake into the DPU OS via bf.conf.

    `peer_count` is the number of in-band DPUs on the same host
    (including this one). On a single-DPU host (peer_count <= 1) we
    keep the historical `<host>-dpu` name so existing deployments
    aren't renamed on re-flash. Multi-DPU hosts number every DPU,
    1-indexed, via `_multi_dpu_hostname_suffix`:

        single-DPU host:   worker1-dpu
        multi-DPU host:    worker1-dpu1, worker1-dpu2, …

    Priority:
      1. Redfish serial (BMC DPUs — stable hardware identifier).
      2. `{host_hostname}-dpu[<suffix>]` for in-band DPUs.
      3. `dpu-{id}` fallback when nothing better is known.
    """
    import re

    if dpu.serial_number:
        return dpu.serial_number

    if getattr(dpu, "access_mode", None) == "in-band":
        host = (getattr(dpu, "host_hostname", None) or "").strip()
        if host:
            # Take only the first DNS label ("worker1.lab.example" → "worker1")
            # and strip anything not hostname-safe.
            host = host.split(".", 1)[0]
            host = re.sub(r"[^A-Za-z0-9-]+", "-", host).strip("-").lower()
            if host:
                if peer_count <= 1:
                    return f"{host}-dpu"
                return f"{host}-dpu{_multi_dpu_hostname_suffix(dpu)}"

    return f"dpu-{dpu.id}"


def build_render_context(
    *,
    dpu: Dpu,
    settings: ProjectDpuSettings,
    ssh_credential: SSHCredential | None,
    ubuntu_password_plaintext: str,
    decrypt: callable,
    peer_dpu_count: int = 1,
) -> RenderContext:
    """Assemble the Jinja context for a single DPU flash.

    `decrypt` is passed in so tests can stub Fernet without pulling in the
    full app config chain. `peer_dpu_count` is the number of in-band DPUs
    on the same host (including this one); the renderer uses it to
    decide whether the DPU's hostname needs a numeric suffix.
    """
    hostname = _derive_bfb_hostname(dpu, peer_count=peer_dpu_count)
    mtu = int(settings.high_speed_mtu or 9000)

    dns_list = list(settings.dns_servers or [])
    dns_ip = ",".join(dns_list) if dns_list else ""
    # If the user never filled in a search-domain field, leave blank — netplan
    # accepts an empty `Domains=` line.
    dns_domains = ""

    ntp_list = list(settings.ntp_servers or [])
    # systemd-timesyncd expects space-separated NTP servers on the `NTP=` line.
    ntp_servers = " ".join(ntp_list) if ntp_list else ""

    vlans: list[VlanContext] = []
    # Fixed rows — external + internal — live on flat columns.
    for iface in ("external", "internal"):
        vid = getattr(settings, f"{iface}_vlan_id", None)
        uplink = getattr(settings, f"{iface}_vlan_uplink", None)
        ip = getattr(dpu, f"{iface}_vlan_ipv4", None) or getattr(dpu, f"{iface}_vlan_ipv6", None)
        if vid is None or not uplink or not ip:
            continue
        vlans.append(VlanContext(name=f"{iface}{vid}", tag=int(vid), uplink=uplink, ip=ip))
    # Extras — project-level list joined by name to per-DPU assignments.
    dpu_extras_by_name = {
        e.get("name"): e for e in (dpu.extra_vlan_interfaces or [])
    }
    for proj_extra in (settings.extra_vlan_interfaces or []):
        name = proj_extra.get("name")
        if not name:
            continue
        vid = proj_extra.get("vlan_id")
        uplink = proj_extra.get("uplink")
        dpu_entry = dpu_extras_by_name.get(name, {})
        ip = dpu_entry.get("ipv4") or dpu_entry.get("ipv6")
        if vid is None or not uplink or not ip:
            continue
        vlans.append(VlanContext(name=f"{name}{vid}", tag=int(vid), uplink=uplink, ip=ip))

    authorized_keys = ""
    if ssh_credential is not None and ssh_credential.private_key_encrypted:
        try:
            pk = decrypt(ssh_credential.private_key_encrypted)
            authorized_keys = public_key_from_private(pk, comment=f"bnk-forge:{ssh_credential.name}")
        except Exception as exc:  # noqa: BLE001 — best-effort; fall through with empty authorized_keys
            logger.warning(
                "Could not derive public key from SSH credential %s: %s",
                ssh_credential.name, exc,
            )

    tmfifo_dpu_ip = derive_tmfifo_dpu_ip(getattr(dpu, "rshim_device", None), dpu=dpu)

    return RenderContext(
        bfb_hostname=hostname,
        dpu_mtu=mtu,
        ubuntu_password_hash=sha512_crypt(ubuntu_password_plaintext),
        host=HostContext(
            oob_net0_ip=None,
            default_gateway=None,
            dns_ip=dns_ip,
            dns_domains=dns_domains,
            ntp_servers=ntp_servers,
            authorized_keys=authorized_keys,
            vlans=vlans,
            tmfifo_dpu_ip=tmfifo_dpu_ip,
        ),
    )


# Handy: produce `new.bfb = <bfb> || <bf.cfg>` as a bytes-producing generator.
def bfb_with_config_stream(bfb_path: os.PathLike, bf_cfg: str, chunk_size: int = 4 * 1024 * 1024):
    """Yield bytes of the original BFB followed by the UTF-8-encoded bf.cfg.

    The caller (SFTP upload) streams this directly so we never materialise
    the full concatenated ~2 GiB blob on disk.
    """
    with open(bfb_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk
    yield bf_cfg.encode("utf-8")


def _b64_helper(s: str) -> str:  # pragma: no cover — reserved for future template use
    return base64.b64encode(s.encode()).decode()


# ── Lint ───────────────────────────────────────────────────────────────────
#
# A bad bf.conf can brick a DPU into an "installed but unreachable" state —
# broken netplan YAML means no management network, an unbalanced heredoc
# means the whole installer script fails silently. The three checks below
# catch the common classes of mistake BEFORE we SFTP anything to the BMC:
#
#   1. `bash -n <rendered>` — syntax-only parse. Catches unbalanced heredoc
#      delimiters, unclosed quotes, unmatched braces.
#   2. YAML parse of each heredoc block whose redirect target ends in
#      `.yaml` / `.yml` — catches indentation errors in netplan files.
#   3. Unresolved Jinja scan — belt-and-braces over StrictUndefined in case
#      a template uses dynamic lookups the StrictUndefined engine can't
#      flag (e.g. `{{ hostvars[name].foo }}` where `foo` is missing).

# Matches:  cat << EOF > /path.yaml    cat << 'EOF' >> /path    cat <<-EOF > path
_HEREDOC_RE = re.compile(
    r"""^\s*cat\s*<<(?P<strip>-?)\s*(?P<q>['\"]?)(?P<delim>\w+)(?P=q)\s*>>?\s*(?P<path>\S+)\s*$""",
)


def lint_bf_conf(rendered: str) -> list[str]:
    """Return a list of human-readable lint issues. Empty list means OK.

    Non-fatal: the caller decides whether to abort. `DpuFlashService`
    aborts the flash rather than corrupt a DPU.
    """
    issues: list[str] = []
    issues.extend(_lint_unresolved_jinja(rendered))
    issues.extend(_lint_bash_syntax(rendered))
    issues.extend(_lint_yaml_heredocs(rendered))
    return issues


def _lint_unresolved_jinja(rendered: str) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(rendered.splitlines(), start=1):
        if "{{" in line or "{%" in line:
            out.append(f"line {i}: unresolved Jinja token: {line.strip()!r}")
    return out


def _lint_bash_syntax(rendered: str) -> list[str]:
    """Run `bash -n` over the rendered content. No execution."""
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write(rendered)
        tmp_path = f.name
    try:
        proc = subprocess.run(
            ["bash", "-n", tmp_path],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        logger.warning("bash not available on PATH — skipping bf.conf bash syntax check")
        return []
    except subprocess.TimeoutExpired:
        return ["bash -n syntax check timed out (> 15s)"]
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if proc.returncode == 0:
        return []
    # bash prints one line per error, usually prefixed with the tmp-path name.
    # Strip the path prefix so messages are portable / comparable in tests.
    cleaned = proc.stderr.replace(tmp_path, "<rendered>").strip()
    return [f"bash -n: {line.strip()}" for line in cleaned.splitlines() if line.strip()]


def _lint_yaml_heredocs(rendered: str) -> list[str]:
    """Extract each `cat << EOF > foo.yaml` heredoc and yaml.safe_load it.

    Non-YAML heredocs (shell scripts, systemd unit files, cloud-init YAML
    fragments embedded in a write_files list, etc.) are skipped — only
    files whose redirect target literally ends in `.yaml` or `.yml` get
    validated.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover — yaml is a hard dep of the backend
        return []

    issues: list[str] = []
    lines = rendered.splitlines()
    i = 0
    while i < len(lines):
        m = _HEREDOC_RE.match(lines[i])
        if not m:
            i += 1
            continue
        delim = m.group("delim")
        path = m.group("path")
        # The shell `cat <<-` variant strips *leading tabs* from the body +
        # closing delimiter. We don't strip tabs here because netplan YAML
        # in the seeded templates uses spaces, but we do accept the closing
        # delimiter with leading whitespace to match `<<-` semantics.
        start_line_no = i + 1
        body: list[str] = []
        i += 1
        closed = False
        while i < len(lines):
            line = lines[i]
            if line.strip() == delim:
                closed = True
                i += 1
                break
            body.append(line)
            i += 1
        if not closed:
            issues.append(
                f"line {start_line_no}: heredoc delimiter {delim!r} (→ {path}) never closed"
            )
            continue
        if not (path.endswith(".yaml") or path.endswith(".yml")):
            continue
        try:
            # netplan files must parse as a single YAML doc. `safe_load`
            # raises YAMLError on bad indentation, unclosed mappings, etc.
            yaml.safe_load("\n".join(body))
        except yaml.YAMLError as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            issues.append(
                f"line {start_line_no}: YAML heredoc → {path} failed to parse: {first_line}"
            )
    return issues
