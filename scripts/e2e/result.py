"""Per-step result + report writers.

Every step function returns a `StepResult`. The orchestrator collects
them, prints a live progress line, and at the end writes:

  - `report.json` — machine-readable, full payload capture.
  - `report.md` — human-readable summary an operator can paste.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Single source of truth for the status visuals used across the
# orchestrator's live log (ASCII), the Markdown report (emoji),
# and the PDF (hex colours). Anything that wants to render a status
# imports from here; keeps the four channels in sync.
STATUS_LOG_ICONS = {"ok": "✓", "warn": "!", "failed": "✗", "skipped": "·"}
STATUS_MD_ICONS = {"ok": "✅", "warn": "⚠️", "failed": "❌", "skipped": "⏭️"}
STATUS_PDF_COLORS = {
    "ok": "#10b981",
    "warn": "#f59e0b",
    "failed": "#ef4444",
    "skipped": "#94a3b8",
}


@dataclass
class StepResult:
    name: str
    status: str           # "ok" | "skipped" | "warn" | "failed"
    started_at: float
    duration_s: float
    summary: str = ""
    # Bag of step-specific data (project_id, dpu_ids, matrix snapshot,
    # raw API responses…). Anything JSON-serialisable.
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunReport:
    started_at: float
    finished_at: float
    config_path: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.finished_at - self.started_at

    @property
    def overall_status(self) -> str:
        if any(s.status == "failed" for s in self.steps):
            return "failed"
        if any(s.status == "warn" for s in self.steps):
            return "warn"
        return "ok"


# ── Writers ────────────────────────────────────────────────────────


def write_json_report(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "started_at": report.started_at,
                "finished_at": report.finished_at,
                "duration_s": round(report.duration_s, 2),
                "overall_status": report.overall_status,
                "config_path": report.config_path,
                "steps": [asdict(s) for s in report.steps],
            },
            f, indent=2, default=str,
        )


def write_md_report(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.strftime(
        "%Y-%m-%d %H:%M:%S UTC", time.gmtime(report.started_at),
    )
    icon = STATUS_MD_ICONS
    lines: list[str] = []
    lines.append(f"# bnk-forge e2e — {report.overall_status.upper()}")
    lines.append("")
    lines.append(f"- **Started:** {started}")
    lines.append(f"- **Duration:** {report.duration_s:.1f}s")
    lines.append(f"- **Config:** `{report.config_path}`")
    lines.append("")
    lines.append("| # | Step | Status | Duration | Summary |")
    lines.append("| --- | --- | --- | --- | --- |")
    for i, step in enumerate(report.steps, 1):
        ic = icon.get(step.status, step.status)
        summary = step.summary.replace("|", "\\|") if step.summary else ""
        if step.error:
            _escaped_error = step.error.replace("|", "\\|")
            summary = f"{summary} — `{_escaped_error}`".strip(" —")
        lines.append(
            f"| {i} | {step.name} | {ic} {step.status} "
            f"| {step.duration_s:.1f}s | {summary} |",
        )
    lines.append("")
    # Per-step evidence as collapsible details.
    for i, step in enumerate(report.steps, 1):
        if not step.evidence:
            continue
        lines.append(f"### Step {i} evidence — `{step.name}`")
        lines.append("")
        lines.append("<details><summary>show</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(step.evidence, indent=2, default=str))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── Helper for steps ───────────────────────────────────────────────


class StepRecorder:
    """Context manager: records duration + catches exceptions into a
    StepResult so a step body never has to remember the bookkeeping."""

    def __init__(self, name: str):
        self.name = name
        self._start = 0.0
        self.summary = ""
        self.evidence: dict[str, Any] = {}
        self._error: str | None = None
        self._status = "ok"

    def __enter__(self) -> StepRecorder:
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Returning False propagates the exception. The orchestrator
        # catches it at the per-step boundary and writes a final
        # report — but we still need the status flipped here.
        if exc is not None:
            self._error = f"{exc_type.__name__}: {exc}"
            self._status = "failed"
        return False

    def warn(self, summary: str) -> None:
        self._status = "warn"
        self.summary = summary

    def ok(self, summary: str) -> None:
        self._status = "ok"
        self.summary = summary

    def skip(self, summary: str) -> None:
        self._status = "skipped"
        self.summary = summary

    def fail(self, summary: str, *, error: str | None = None) -> None:
        """Record a *business* failure — the API call succeeded but
        the result is bad (no DPU hosts found, matrix had failures,
        …). Use this instead of `raise` so evidence collected up to
        this point survives into the final report; the orchestrator
        will still see status='failed' and stop."""
        self._status = "failed"
        self.summary = summary
        if error is not None:
            self._error = error

    def record(self, **kv: Any) -> None:
        self.evidence.update(kv)

    def to_result(self) -> StepResult:
        return StepResult(
            name=self.name,
            status=self._status,
            started_at=self._start,
            duration_s=round(time.time() - self._start, 3),
            summary=self.summary,
            evidence=self.evidence,
            error=self._error,
        )
