"""Always-on PDF test report.

Run sequence:
  1. Cover page: overall pass/fail, timestamp, project name, totals.
  2. Network topology diagram with DPU IPs + firmware versions
     (DOCA / NIC / BMC) — built from the steps' captured evidence.
  3. Per-step results table with status pills, durations, summaries.
  4. Per-step evidence excerpts so the operator can root-cause
     without opening the JSON sidecar.

reportlab is the only dependency — pure Python, no system libs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .diagram import build_topology
from .result import STATUS_PDF_COLORS, RunReport

_STATUS_COLOUR = {k: colors.HexColor(v) for k, v in STATUS_PDF_COLORS.items()}


def write_pdf_report(
    report: RunReport,
    path: Path,
    *,
    config_summary: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        # 0.4" left/right margins give the topology diagram enough
        # frame width (~554pt) to show the full DOCA value inside the
        # worker boxes. Top/bottom stay 0.6" for a comfortable header.
        leftMargin=0.4 * inch, rightMargin=0.4 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title="bnk-forge e2e report",
        author="scripts.e2e",
    )
    styles = _styles()
    story: list[Any] = []

    _add_cover(story, report, styles, config_summary or {})
    _add_topology(story, report, styles, config_summary or {})
    _add_results_table(story, report, styles)
    _add_evidence_section(story, report, styles)
    doc.build(story)


# ── Sections ───────────────────────────────────────────────────────


def _add_cover(
    story: list[Any], report: RunReport, styles: dict[str, ParagraphStyle],
    cfg: dict[str, Any],
) -> None:
    overall = report.overall_status.upper()
    overall_colour = _STATUS_COLOUR.get(report.overall_status, colors.black)
    story.append(Paragraph("bnk-forge e2e test report", styles["title"]))
    story.append(Paragraph(
        f"<font color='{overall_colour.hexval()}'><b>{overall}</b></font>",
        styles["h1"],
    ))
    story.append(Spacer(1, 12))

    counts = _count_by_status(report)
    summary_rows = [
        ["Started", _fmt_timestamp(report.started_at)],
        ["Duration", f"{report.duration_s:.1f} s"],
        ["Steps", str(len(report.steps))],
        ["Pass / Warn / Fail / Skip",
         f"{counts['ok']} / {counts['warn']} / "
         f"{counts['failed']} / {counts['skipped']}"],
    ]
    if cfg.get("project_name"):
        summary_rows.append(["bnk-forge project", cfg["project_name"]])
    if cfg.get("bnk_forge_url"):
        summary_rows.append(["bnk-forge URL", cfg["bnk_forge_url"]])
    if cfg.get("bf_template"):
        summary_rows.append(["bf.conf template", cfg["bf_template"]])
    if cfg.get("bfb_image"):
        summary_rows.append(["BFB image", cfg["bfb_image"]])
    summary_rows.append(["Config", report.config_path])

    story.append(_kv_table(summary_rows))
    story.append(Spacer(1, 12))


def _add_topology(
    story: list[Any], report: RunReport, styles: dict[str, ParagraphStyle],
    cfg: dict[str, Any],
) -> None:
    story.append(Paragraph("Network topology", styles["h2"]))
    drawing = build_topology(
        report.steps,
        jumphost_host=cfg.get("jumphost_host"),
        bnk_forge_url=cfg.get("bnk_forge_url", ""),
        bnk_forge_build=cfg.get("bnk_forge_build") or {},
    )
    # Left-align so the diagram starts flush with the page margin
    # (Drawing flowable defaults to centred, which read as an
    # unintentional indent next to the cover-page table).
    drawing.hAlign = "LEFT"
    story.append(drawing)
    story.append(Spacer(1, 12))


def _add_results_table(
    story: list[Any], report: RunReport, styles: dict[str, ParagraphStyle],
) -> None:
    story.append(PageBreak())
    story.append(Paragraph("Step results", styles["h2"]))
    rows = [["#", "Step", "Status", "Duration", "Summary"]]
    for i, step in enumerate(report.steps, 1):
        rows.append([
            str(i),
            step.name,
            step.status.upper(),
            f"{step.duration_s:.1f}s",
            _wrap(step.summary or step.error or "", styles["body"]),
        ])
    t = Table(
        rows,
        colWidths=[0.4 * inch, 1.6 * inch, 0.7 * inch, 0.7 * inch, 3.6 * inch],
        repeatRows=1,
    )
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8fafc")]),
    ])
    # Colour the Status column per status.
    for i, step in enumerate(report.steps, 1):
        bg = _STATUS_COLOUR.get(step.status, colors.grey)
        style.add("BACKGROUND", (2, i), (2, i), bg)
        style.add("TEXTCOLOR", (2, i), (2, i), colors.white)
        style.add("FONTNAME", (2, i), (2, i), "Helvetica-Bold")
        style.add("ALIGN", (2, i), (2, i), "CENTER")
    t.setStyle(style)
    story.append(t)


def _add_evidence_section(
    story: list[Any], report: RunReport, styles: dict[str, ParagraphStyle],
) -> None:
    if not any(s.evidence for s in report.steps):
        return
    story.append(Spacer(1, 16))
    story.append(Paragraph("Evidence", styles["h2"]))
    for i, step in enumerate(report.steps, 1):
        if not step.evidence:
            continue
        story.append(Paragraph(
            f"{i}. {step.name} — {step.status}",
            styles["h3"],
        ))
        if step.error:
            story.append(Paragraph(
                f"<b>error:</b> {_escape(step.error)}", styles["body"],
            ))
        # Render the evidence dict as truncated JSON in a monospace
        # block. `Preformatted` keeps the json.dumps indent intact —
        # `Paragraph` would collapse the leading spaces. Long blobs
        # (e.g. flash logs) are clipped at 1500 chars.
        excerpt = _summarise_evidence(step.evidence)
        story.append(Preformatted(excerpt, styles["mono"]))
        story.append(Spacer(1, 6))


# ── Helpers ────────────────────────────────────────────────────────


def _count_by_status(report: RunReport) -> dict[str, int]:
    out = {"ok": 0, "warn": 0, "failed": 0, "skipped": 0}
    for s in report.steps:
        out[s.status] = out.get(s.status, 0) + 1
    return out


def _summarise_evidence(evidence: dict[str, Any], cap: int = 1500) -> str:
    text = json.dumps(evidence, indent=2, default=str)
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n… (truncated, {len(text) - cap} more chars)"


def _kv_table(rows: list[list[str]]) -> Table:
    t = Table(rows, colWidths=[2.0 * inch, 4.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    return t


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace("\n", "<br/>")
    )


def _wrap(text: str, style: ParagraphStyle) -> Any:
    return Paragraph(_escape(text or ""), style)


def _fmt_timestamp(ts: float) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontSize=20, leading=24, spaceAfter=4,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"],
            fontSize=18, leading=22,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontSize=14, leading=18, spaceBefore=8, spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"],
            fontSize=11, leading=14, spaceBefore=8, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontSize=9, leading=11,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["BodyText"],
            fontName="Courier", fontSize=8, leading=10,
            leftIndent=8, rightIndent=8,
            backColor=colors.HexColor("#f8fafc"),
        ),
    }
