"""Network topology diagram, drawn from collected step evidence.

Produces a reportlab Drawing the PDF report can embed. Three rows:

  Row 1 — bnk-forge server (with optional jumphost)
  Row 2 — worker nodes (one box each)
  Row 3 — DPUs nested inside the worker row, with BMC IP / DPU OS IP /
          serial / firmware (DOCA / NIC / BMC) on each.

Pulls data from the StepResult evidence dicts the harness collects:
  - `discover_dpus`   → DPU id, BMC IP, DPU OS IP
  - `probe_dpu_os`    → firmware (post-flash overrides post-discovery)
  - `register_hosts`  → worker node IPs
  - `create_jumphost` → optional jumphost host/user
"""

from __future__ import annotations

import re
from typing import Any

from reportlab.graphics.shapes import (
    Drawing, Group, Line, Rect, String,
)
from reportlab.lib import colors

from .result import StepResult


# Layout constants — tuned to fit inside a letter-size PDF with the
# 0.4-inch margins `pdf_report.SimpleDocTemplate` uses (frame ≈ 554pt).
# Anything wider gets scaled or clipped by Platypus, which silently
# eats the right-hand side of body text.
#
# Worker box width is sized to hold the full DOCA value
# (`bf-bundle-2.9.2-32_FUR-A_25.02_ubuntu-22.04_prod`, ~48 chars at
# FONT_SM ≈ 240pt incl. label). The middle gap (40pt) holds the DAC
# cable's `p0  200Gbps` label.
PAGE_W = 550
ROW_GAP = 16
WORKER_BOX_W = 235
WORKER_BOX_PAD = 8
DPU_BOX_W = 235
# Cable label `p0  200Gbps` measures ~50pt at FONT_SM-bold; 60pt of
# middle gap leaves ~5pt of breathing room each side so the label
# never overlaps the worker boxes.
WORKER_MIDDLE_GAP = 60
DPU_LINE_H = 11
HEADER_H = 16
FONT_SM = 8
FONT_MD = 9
FONT_LG = 10


def build_topology(
    steps: list[StepResult],
    *,
    jumphost_host: str | None = None,
    bnk_forge_url: str = "",
    bnk_forge_build: dict[str, str | None] | None = None,
) -> Drawing:
    """Return a reportlab Drawing rendering the topology found in `steps`.

    The drawing's height adapts to the DPU count so multi-DPU rigs stay
    readable. Width is fixed at letter content area.
    """
    workers, dpus_by_worker = _extract_topology(steps)

    # Only show the jumphost row when it's actually in use — i.e. the
    # `create_jumphost` step ran and didn't skip. A configured-but-
    # skipped jumphost would otherwise leave a misleading empty box.
    if jumphost_host:
        jh_step = next(
            (s for s in steps if s.name == "create_jumphost"), None,
        )
        if jh_step is None or jh_step.status not in ("ok", "warn"):
            jumphost_host = None

    # Compute height: header + bnk-forge box + worker row + footer.
    # The DPU box's row count varies (VLAN rows are conditional), so
    # ask each DPU what it would render and take the max — undersized
    # boxes truncate text, oversized ones waste page.
    max_dpus = max((len(v) for v in dpus_by_worker.values()), default=1)
    max_dpu_rows = 1
    for dpus in dpus_by_worker.values():
        for dpu in dpus:
            max_dpu_rows = max(max_dpu_rows, len(_dpu_lines(dpu)))
    dpu_total_h = max(1, max_dpus) * (HEADER_H + max_dpu_rows * DPU_LINE_H + 4)
    # Worker row = its own title bar + the stacked DPU boxes flush
    # against it. No interior padding — the DPU header sits directly
    # below the worker header so the two read as one composite box.
    worker_row_h = HEADER_H + dpu_total_h
    bnk_lines = _bnk_forge_lines(bnk_forge_url, bnk_forge_build or {})
    # Bigger body text for the bnk-forge box — only 3 lines, plenty
    # of room, and operators want to read the version/branch/commit
    # without zooming. 13pt line-height keeps comfortable spacing.
    BNK_LINE_H = 13
    BNK_FONT = FONT_MD
    bnk_box_h = HEADER_H + len(bnk_lines) * BNK_LINE_H + 8
    # Reserve enough below the worker row that the footer note can sit
    # there without overlapping the worker box. 28pt = ~14pt gap +
    # 8pt text + descent.
    FOOTER_H = 28
    height = (
        4  # tiny top margin (no in-drawing title — pdf_report prints one)
        + bnk_box_h
        + ROW_GAP
        + (32 if jumphost_host else 0)  # jumphost box
        + (ROW_GAP if jumphost_host else 0)
        + worker_row_h
        + FOOTER_H
    )

    d = Drawing(PAGE_W, height)
    # The PDF section already prints a "Network topology" H2 above
    # this Drawing — don't duplicate it here. Reserve a tiny top
    # margin so the bnk-forge box doesn't kiss the H2's bottom rule.
    cursor_y = height - 4

    # bnk-forge server. Build identity comes from `_bnk_forge_lines`
    # — version/branch/commit, captured during step_login from the
    # local repo, surface here so two runs are easy to compare.
    # Wider box so long commit subjects don't have to be truncated and
    # the platform line has room to breathe. Sized to fit comfortably
    # within PAGE_W with a small symmetric margin.
    bnk_w = 530
    d.add(_box(
        x=(PAGE_W - bnk_w) / 2,
        y=cursor_y - bnk_box_h,
        w=bnk_w, h=bnk_box_h,
        title="bnk-forge",
        lines=bnk_lines,
        title_color=colors.HexColor("#1e3a8a"),
        body_color=colors.HexColor("#e0e7ff"),
        body_font_size=BNK_FONT,
        body_line_h=BNK_LINE_H,
    ))
    bnk_anchor = (PAGE_W / 2, cursor_y - bnk_box_h)
    cursor_y -= bnk_box_h + ROW_GAP

    # Optional jumphost.
    if jumphost_host:
        jh_box_h = 32
        d.add(_box(
            x=(PAGE_W - 220) / 2,
            y=cursor_y - jh_box_h,
            w=220, h=jh_box_h,
            title="jumphost",
            lines=[jumphost_host],
            title_color=colors.HexColor("#7c2d12"),
            body_color=colors.HexColor("#fef3c7"),
        ))
        # Line from bnk-forge → jumphost.
        d.add(Line(
            bnk_anchor[0], bnk_anchor[1],
            PAGE_W / 2, cursor_y,
            strokeColor=colors.grey, strokeWidth=1,
        ))
        bnk_anchor = (PAGE_W / 2, cursor_y - jh_box_h)
        cursor_y -= jh_box_h + ROW_GAP

    # Worker row. The 2-worker rig is the common case and uses a
    # non-uniform layout: small side margins, a wider middle gap so
    # the DAC cable's `p0  200Gbps` label has room. Other layouts
    # fall back to uniform spacing.
    n = max(1, len(workers))
    worker_y = cursor_y - worker_row_h
    if len(workers) == 2:
        side = (PAGE_W - 2 * WORKER_BOX_W - WORKER_MIDDLE_GAP) / 2
        worker_xs = [side, side + WORKER_BOX_W + WORKER_MIDDLE_GAP]
        spacing = WORKER_MIDDLE_GAP  # used by DAC cable math below
    else:
        spacing = (PAGE_W - n * WORKER_BOX_W) / (n + 1)
        worker_xs = [spacing + i * (WORKER_BOX_W + spacing) for i in range(n)]

    for i, w_ip in enumerate(workers):
        wx = worker_xs[i]
        # Worker frame.
        d.add(Rect(
            wx, worker_y, WORKER_BOX_W, worker_row_h,
            strokeColor=colors.HexColor("#475569"),
            strokeWidth=1.2, fillColor=colors.HexColor("#f1f5f9"),
        ))
        d.add(Rect(
            wx, worker_y + worker_row_h - HEADER_H,
            WORKER_BOX_W, HEADER_H,
            strokeColor=colors.HexColor("#475569"), strokeWidth=1.2,
            fillColor=colors.HexColor("#cbd5e1"),
        ))
        d.add(String(
            wx + 6, worker_y + worker_row_h - HEADER_H + 4,
            f"worker  {w_ip}",
            fontName="Helvetica-Bold", fontSize=FONT_MD,
        ))

        # Connect bnk-forge to this worker.
        d.add(Line(
            bnk_anchor[0], bnk_anchor[1],
            wx + WORKER_BOX_W / 2, worker_y + worker_row_h,
            strokeColor=colors.grey, strokeWidth=0.8,
        ))

        # DPUs nested inside, full worker-width and stacked at the
        # bottom — they read as the worker box's "lower half" so the
        # DAC cables visibly attach to the DPU edges.
        dpu_y = worker_y
        for dpu in dpus_by_worker.get(w_ip, []):
            box_h = HEADER_H + len(_dpu_lines(dpu)) * DPU_LINE_H + 4
            d.add(_dpu_box(wx, dpu_y, WORKER_BOX_W, box_h, dpu))
            dpu_y += box_h

        # Worker had no DPUs detected — leave a "no DPUs" hint.
        if not dpus_by_worker.get(w_ip):
            d.add(String(
                wx + WORKER_BOX_W / 2,
                worker_y + worker_row_h / 2,
                "(no DPUs found)",
                fontName="Helvetica-Oblique", fontSize=FONT_SM,
                fillColor=colors.HexColor("#94a3b8"),
                textAnchor="middle",
            ))

    # 2 × DAC cables between DPUs in the common 2-worker rig — one
    # per uplink port (p0↔p0, p1↔p1). Drawn as fat solid lines with
    # the negotiated link speed centered on each cable.
    if len(workers) == 2:
        left_dpus = dpus_by_worker.get(workers[0], [])
        right_dpus = dpus_by_worker.get(workers[1], [])
        if left_dpus and right_dpus:
            # Cable endpoints attach to the inner edges of the two
            # worker/DPU boxes (which are the same since DPU spans the
            # full worker width).
            x1 = worker_xs[0] + WORKER_BOX_W
            x2 = worker_xs[1]
            mid_y = worker_y + 30
            # Wider vertical separation now that the cables are thick
            # (3pt) and carry their own speed label.
            # Will we draw the central `bond0` label between the
            # cables? If so, push p1's port label *below* its cable
            # so the gap is free for the bond name.
            has_bond = bool(
                (left_dpus[0].get("lacp") or {}).get("bond_present")
                or (right_dpus[0].get("lacp") or {}).get("bond_present"),
            )
            for dy, port in ((10, "p0"), (-10, "p1")):
                y = mid_y + dy
                speed = _link_speed_for(left_dpus[0], port) or \
                        _link_speed_for(right_dpus[0], port)
                _draw_dac(
                    d, x1, y, x2, y,
                    port=port,
                    link_ok=_link_up_for(left_dpus[0], port)
                            and _link_up_for(right_dpus[0], port),
                    speed=speed,
                    label_above=(port == "p0" or not has_bond),
                )
            # When the probe shows a LAG, label the cables as a
            # bonded pair. Single bold `bond0` sits in the natural
            # 20pt gap between the two cables — no brackets and no
            # verbose mode/rate annotation, both of which were
            # cluttering the small middle gap and bleeding into the
            # worker boxes. Two parallel coloured cables flanking a
            # centred bond label reads unambiguously as a bond.
            # Anchored to the LACP shape from
            # `dpu_os_probe_service._parse_lacp_sections`.
            lag = (
                _lag_summary(left_dpus[0].get("lacp") or {})
                or _lag_summary(right_dpus[0].get("lacp") or {})
            )
            if lag:
                bond_name, _ = lag
                d.add(String(
                    (x1 + x2) / 2, mid_y - 3, bond_name,
                    fontName="Helvetica-Bold", fontSize=FONT_SM,
                    textAnchor="middle",
                    fillColor=colors.HexColor("#1f2937"),
                ))

    # Footer note — sits *below* the worker row, not on top of it.
    # Anchoring to worker_y guarantees we follow the worker box even
    # if the height calc changes later.
    d.add(String(
        PAGE_W / 2, worker_y - 14,
        "Thin grey lines = management path · thick coloured = DAC uplinks",
        fontName="Helvetica-Oblique", fontSize=FONT_SM,
        textAnchor="middle", fillColor=colors.grey,
    ))
    return d


# ── Helpers ────────────────────────────────────────────────────────


def _extract_topology(
    steps: list[StepResult],
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """Walk the recorded evidence to build (worker_ips, dpus_by_worker).

    DPU records merge data from `discover_dpus` (BMC/OS IPs) and the
    most-recent `probe_dpu_os*` step (firmware). Post-flash data wins
    when present.
    """
    by_step = {s.name: s for s in steps}
    workers = _workers_from_evidence(by_step)
    dpu_records = _dpus_from_evidence(by_step)

    # Merge firmware + lacp + IP overrides from probe (post-flash
    # preferred) into discover output. Discover only knows the
    # at-registration state; the freshest IPs (DPU OS post-reboot,
    # BMC IP harvested from `ipmitool lan print`) and firmware come
    # from probe_dpu_os.
    fw_by_id: dict[int, dict[str, Any]] = {}
    lacp_by_id: dict[int, dict[str, Any]] = {}
    os_ip_by_id: dict[int, str] = {}
    bmc_ip_by_id: dict[int, str] = {}
    for probe_name in ("probe_dpu_os_post_flash", "probe_dpu_os"):
        ev = _evidence_of(by_step, probe_name)
        for d in ev.get("dpus", []):
            did = d.get("id")
            fw = d.get("firmware") or {}
            if fw and did not in fw_by_id:
                fw_by_id[did] = fw
            lacp = d.get("lacp") or {}
            if lacp and did not in lacp_by_id:
                lacp_by_id[did] = lacp
            os_ip = d.get("dpu_os_ip")
            if os_ip and did not in os_ip_by_id:
                os_ip_by_id[did] = os_ip
            bmc_ip = d.get("bmc_ip")
            if bmc_ip and did not in bmc_ip_by_id:
                bmc_ip_by_id[did] = bmc_ip
    for d in dpu_records:
        d["firmware"] = fw_by_id.get(d["id"], {})
        d["lacp"] = lacp_by_id.get(d["id"], {})
        if os_ip_by_id.get(d["id"]):
            d["dpu_os_ip"] = os_ip_by_id[d["id"]]
        if bmc_ip_by_id.get(d["id"]):
            d["bmc_ip"] = bmc_ip_by_id[d["id"]]

    # Group DPUs under their worker. We don't have an explicit
    # worker_ip → dpu link in the harness evidence, so distribute
    # round-robin when ambiguous; the bnk-forge UI shows the same
    # mapping under host_node_ip when available.
    dpus_by_worker: dict[str, list[dict[str, Any]]] = {w: [] for w in workers}
    for i, d in enumerate(dpu_records):
        host_ip = d.get("host_node_ip")
        if host_ip in dpus_by_worker:
            dpus_by_worker[host_ip].append(d)
        elif workers:
            dpus_by_worker[workers[i % len(workers)]].append(d)
    return workers, dpus_by_worker


def _evidence_of(
    by_step: dict[str, StepResult], name: str,
) -> dict[str, Any]:
    """Step's `.evidence` dict, or `{}` when the step didn't run."""
    step = by_step.get(name)
    return step.evidence if step is not None else {}


def _workers_from_evidence(by_step: dict[str, StepResult]) -> list[str]:
    # Discovery step records the IPs it scanned; register_hosts
    # records which were actually registered. Prefer registered.
    ev = _evidence_of(by_step, "run_discovery_workers")
    return list(ev.get("dpu_host_ips") or [])


def _dpus_from_evidence(
    by_step: dict[str, StepResult],
) -> list[dict[str, Any]]:
    ev = _evidence_of(by_step, "discover_dpus")
    return [dict(d) for d in (ev.get("dpus") or [])]


def _box(
    *, x: float, y: float, w: float, h: float,
    title: str, lines: list[str],
    title_color: colors.Color, body_color: colors.Color,
    body_font_size: int = FONT_SM,
    body_line_h: int = DPU_LINE_H,
) -> Group:
    g = Group()
    g.add(Rect(x, y, w, h, strokeColor=title_color, strokeWidth=1,
               fillColor=body_color))
    g.add(Rect(x, y + h - HEADER_H, w, HEADER_H,
               strokeColor=title_color, strokeWidth=1,
               fillColor=title_color))
    g.add(String(
        x + 6, y + h - HEADER_H + 4, title,
        fontName="Helvetica-Bold", fontSize=FONT_MD,
        fillColor=colors.white,
    ))
    for i, line in enumerate(lines):
        g.add(String(
            x + 6, y + h - HEADER_H - 12 - i * body_line_h,
            line, fontName="Helvetica", fontSize=body_font_size,
        ))
    return g


def _bnk_forge_lines(
    url: str, build: dict[str, str | None],
) -> list[str]:
    """Body lines for the bnk-forge box: URL, version/branch, commit
    (with the subject wrapped onto continuation lines when long), and
    platform / OS info when captured."""
    lines = [url or "(local)"]
    version, branch = build.get("version"), build.get("branch")
    commit, subject = build.get("commit"), build.get("commit_subject")
    platform = build.get("platform")
    if version or branch:
        lines.append(f"version: {version or '—'}    branch: {branch or '—'}")
    if commit:
        subj = (subject or "").strip()
        head = f"commit: {commit}"
        if subj:
            # Soft-wrap the subject so the full line is visible. Width
            # is sized for the 530pt-wide box at FONT_MD: ~95 chars on
            # the first line (after `commit: <hash>  ` prefix) and
            # ~100 chars on continuation lines (indented to the same
            # column for readability).
            FIRST = 95
            CONT = 100
            indent = " " * len(head + "  ")
            first_chunk = subj[:FIRST]
            rest = subj[FIRST:]
            lines.append(f"{head}  {first_chunk}".rstrip())
            while rest:
                chunk, rest = rest[:CONT], rest[CONT:]
                lines.append(f"{indent}{chunk}")
        else:
            lines.append(head)
    host = build.get("host")
    if host and host != "localhost":
        lines.append(f"host: {host}")
    if platform:
        lines.append(f"platform: {platform}")
    return lines


def _lag_summary(lacp: dict[str, Any]) -> tuple[str, str | None] | None:
    """Two-tier LAG identifier (`name`, `details`) for the bond label,
    or None when the probe shows no bond. Reads the shape produced by
    `dpu_os_probe_service._parse_lacp_sections`:

      LAG      → `{bond_present: True, bond_name: "bond0",
                   mode: "IEEE 802.3ad …", lacp_rate: "fast"}`
      non-LAG  → `{bond_present: False, …}`

    Split into two lines so the wide form (`802.3ad · LACP fast`)
    doesn't overflow the narrow middle gap and bleed into the worker
    boxes — `name` renders prominently in the bracket, `details`
    smaller above.
    """
    if not lacp or not lacp.get("bond_present"):
        return None
    name = lacp.get("bond_name") or "bond0"
    detail_parts: list[str] = []
    mode = lacp.get("mode") or ""
    # `/proc/net/bonding/bond0` reports a wordy mode line — pull out
    # the standard token (802.3ad / active-backup / balance-*).
    m = re.search(r"\b(802\.3ad|active-backup|balance-\w+)\b", mode)
    if m:
        detail_parts.append(m.group(1))
    rate = lacp.get("lacp_rate")
    if rate:
        detail_parts.append(f"LACP {rate}")
    details = " · ".join(detail_parts) if detail_parts else None
    return name, details


def _link_up_for(dpu: dict[str, Any], port: str) -> bool:
    """True when probe evidence shows `port` (p0/p1) negotiated a link.

    Two LACP shapes exist (see `dpu_os_probe_service._parse_lacp_sections`):
      - LAG: members[].mii_status == "up"
      - non-LAG: uplinks[].link == "yes"
    """
    lacp = dpu.get("lacp") or {}
    if lacp.get("bond_present"):
        for m in lacp.get("members") or []:
            if m.get("name") == port and (m.get("mii_status") or "").lower() == "up":
                return True
        return False
    for u in lacp.get("uplinks") or []:
        if u.get("name") == port and (u.get("link") or "").lower() == "yes":
            return True
    return False


def _link_speed_for(dpu: dict[str, Any], port: str) -> str | None:
    """Negotiated link speed for `port`, formatted like `200Gbps`.

    Same two LACP shapes as `_link_up_for`. The raw values come from
    `cat /proc/net/bonding/bond0` (members) or `ethtool` (uplinks),
    which both report e.g. `200000Mb/s`. We normalise to Gbps when
    the rate is a clean multiple of 1000Mb/s.
    """
    lacp = dpu.get("lacp") or {}
    raw: str | None = None
    if lacp.get("bond_present"):
        for m in lacp.get("members") or []:
            if m.get("name") == port:
                raw = m.get("speed")
                break
    else:
        for u in lacp.get("uplinks") or []:
            if u.get("name") == port:
                raw = u.get("speed")
                break
    return _format_link_speed(raw)


def _format_link_speed(raw: str | None) -> str | None:
    """`200000Mb/s` → `200Gbps`; `1000Mb/s` → `1Gbps`. Returns the
    original string when we don't recognise the format, `None` when
    nothing meaningful is available."""
    if not raw:
        return None
    s = raw.strip()
    if not s or s.lower() in ("unknown!", "unknown", "-"):
        return None
    # Pull the leading number and the unit suffix.
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return s
    n = int(digits)
    suffix = s[len(digits):].strip().lower()
    if suffix.startswith("mb"):
        return f"{n // 1000}Gbps" if n >= 1000 and n % 1000 == 0 else f"{n}Mbps"
    if suffix.startswith("gb"):
        return f"{n}Gbps"
    return s


def _draw_dac(
    drawing: Drawing,
    x1: float, y1: float, x2: float, y2: float,
    *, port: str, link_ok: bool, speed: str | None = None,
    label_above: bool = True,
) -> None:
    """Render one DAC cable: thick solid line with `<port>  <speed>`
    centered above (or below, when label_above=False) the cable.

    Green when probe evidence confirms the link is up; grey otherwise.
    The per-end port labels are intentionally dropped — the cables are
    visually distinct (top = p0, bottom = p1) and labelling both ends
    plus the centre crowds the gap.

    `label_above=False` is used by the LAG path so p1's label sits
    *below* its cable, leaving the gap between the two cables free
    for the central `bond0` label.
    """
    color = colors.HexColor("#059669") if link_ok else colors.HexColor("#94a3b8")
    drawing.add(Line(
        x1, y1, x2, y2,
        strokeColor=color, strokeWidth=3,
    ))
    label = f"{port}  {speed}" if speed else port
    y_label = (y1 + y2) / 2 + (4 if label_above else -10)
    drawing.add(String(
        (x1 + x2) / 2, y_label, label,
        fontName="Helvetica-Bold", fontSize=FONT_SM, fillColor=color,
        textAnchor="middle",
    ))


def _dpu_lines(dpu: dict[str, Any]) -> list[str]:
    """Body lines for a DPU box. VLAN rows are emitted only when the
    DPU has them assigned, so the box doesn't pad with empty `vlan: —`
    placeholders for projects that don't use those interfaces."""
    fw = dpu.get("firmware") or {}
    lines = [
        f"BMC IP: {dpu.get('bmc_ip') or '—'}",
        f"DPU OS IP: {dpu.get('dpu_os_ip') or '—'}",
        f"DOCA: {fw.get('doca') or '—'}",
        f"NIC FW: {fw.get('nic') or '—'}",
        f"BMC FW: {fw.get('bmc') or '—'}",
    ]
    ext_id, ext_ip = dpu.get("external_vlan_id"), dpu.get("external_vlan_ipv4")
    if ext_id or ext_ip:
        lines.append(f"ext vlan {ext_id or '—'}: {ext_ip or '—'}")
    int_id, int_ip = dpu.get("internal_vlan_id"), dpu.get("internal_vlan_ipv4")
    if int_id or int_ip:
        lines.append(f"int vlan {int_id or '—'}: {int_ip or '—'}")
    for extra in dpu.get("extra_vlan_interfaces") or []:
        name = extra.get("name") or "vlan"
        vid = extra.get("vlan_id")
        ip = extra.get("ipv4") or extra.get("ipv6")
        lines.append(f"{name} vlan {vid or '—'}: {ip or '—'}")
    lines.append(f"status: {dpu.get('discovery_status') or '—'}")
    return lines


def _dpu_box(
    x: float, y: float, w: float, h: float, dpu: dict[str, Any],
) -> Group:
    # Prefer the hostname the bf.conf template renders into the DPU
    # OS at flash time (e.g. `worker1-dpu`). Falling back to the DB
    # `name` field would surface the *host* hostname which conflates
    # the two devices.
    title = (
        dpu.get("bfb_hostname")
        or dpu.get("name")
        or f"DPU id={dpu.get('id', '?')}"
    )
    return _box(
        x=x, y=y, w=w, h=h,
        title=title[:38],
        lines=_dpu_lines(dpu),
        title_color=colors.HexColor("#581c87"),
        body_color=colors.HexColor("#f3e8ff"),
    )
