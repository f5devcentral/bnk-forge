"""CLI entry point: `python -m scripts.e2e [e2e-config.yaml]`.

Most operators just run `scripts/e2e.sh` instead — that wrapper
finds `e2e-config.yaml`, prompts for confirmation, and forwards
remaining args here.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# ── Preflight: friendly diagnosis when third-party deps aren't
#    installed. We do this *before* the package-internal imports below
#    so a stock CPython without yaml / pydantic / reportlab gives a
#    one-line `pip install` instruction instead of a deep stack trace.
_REQUIRED = [
    ("yaml", "pyyaml"),
    ("pydantic", "pydantic"),
    ("requests", "requests"),
    ("urllib3", "urllib3"),
    ("reportlab", "reportlab"),
]
_missing: list[str] = []
for _module, _pkg in _REQUIRED:
    try:
        __import__(_module)
    except ImportError:
        _missing.append(_pkg)
if _missing:
    _req = Path(__file__).resolve().parent / "requirements.txt"
    print(
        "scripts.e2e: missing dependencies — "
        + ", ".join(_missing) + "\n\n"
        f"  pip install {' '.join(_missing)}\n"
        "  # or, all at once with the pinned set:\n"
        f"  pip install -r {_req}\n",
        file=sys.stderr,
    )
    sys.exit(2)

from .client import BnkForgeClient  # noqa: E402  — after preflight by design
from .cloud_steps import CLOUD_PHASE_STEPS, run_cloud_teardown  # noqa: E402
from .config import load_config  # noqa: E402
from .pdf_report import write_pdf_report  # noqa: E402
from .result import (  # noqa: E402
    STATUS_LOG_ICONS,
    RunReport,
    write_json_report,
    write_md_report,
)
from .steps import ALL_STEPS, PHASE_1_STEPS, PHASE_2_STEPS, Context  # noqa: E402


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.e2e",
        description="End-to-end hardware test for bnk-forge.",
    )
    p.add_argument(
        "config", nargs="?", default="e2e-config.yaml",
        help=(
            "path to YAML config (default: ./e2e-config.yaml). Copy "
            "scripts/e2e/e2e-config.example.yaml as a starting point."
        ),
    )
    p.add_argument(
        "--steps",
        help=(
            "comma-separated step names to run, OR a phase alias "
            "(`phase1`, `phase2`). default: phase1+phase2 (all steps)."
        ),
    )
    p.add_argument(
        "--report-dir", default="e2e-reports",
        help="directory to write report.json + report.md (default: e2e-reports/<timestamp>/)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="print the step plan without running anything",
    )
    p.add_argument(
        "--continue-on-failure", action="store_true",
        help=(
            "keep running remaining steps after a failure. default is "
            "to stop at the first failed step (later steps usually "
            "depend on it)."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


_ALL_STEPS_WITH_CLOUD = ALL_STEPS + CLOUD_PHASE_STEPS


def _select_steps(filter_csv: str | None) -> list[tuple[str, callable]]:
    if not filter_csv:
        return ALL_STEPS
    wanted = [s.strip() for s in filter_csv.split(",") if s.strip()]
    # Phase aliases expand to the underlying step names so the operator
    # can write `--steps phase1` (smoke), `--steps phase2` (lifecycle),
    # or `--steps cloud` (cloud-blueprint vertical).
    expanded: list[str] = []
    for w in wanted:
        if w == "phase1":
            expanded.extend(name for name, _ in PHASE_1_STEPS)
        elif w == "phase2":
            expanded.extend(name for name, _ in PHASE_2_STEPS)
        elif w == "cloud":
            expanded.extend(name for name, _ in CLOUD_PHASE_STEPS)
        else:
            expanded.append(w)
    by_name = dict(_ALL_STEPS_WITH_CLOUD)
    missing = [w for w in expanded if w not in by_name]
    if missing:
        valid = ", ".join(name for name, _ in _ALL_STEPS_WITH_CLOUD)
        raise SystemExit(
            f"unknown step(s): {', '.join(missing)}. valid: {valid} "
            f"(or 'phase1' / 'phase2' / 'cloud')",
        )
    return [(w, by_name[w]) for w in expanded]


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    cfg = load_config(args.config)
    plan = _select_steps(args.steps)

    if args.dry_run:
        print(f"Would run {len(plan)} step(s) against {cfg.bnk_forge_url}:")
        for i, (name, _) in enumerate(plan, 1):
            print(f"  {i:2d}. {name}")
        return 0

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report_dir = Path(args.report_dir) / ts
    print(f"e2e: writing reports to {report_dir}")

    client = BnkForgeClient(cfg.bnk_forge_url)
    ctx = Context(cfg=cfg, client=client)
    report = RunReport(
        started_at=time.time(),
        finished_at=time.time(),
        config_path=str(Path(args.config).resolve()),
    )

    # Determine whether the selected plan includes any cloud steps so we know
    # whether to install the cloud finally-teardown guard.
    _cloud_step_names = {name for name, _ in CLOUD_PHASE_STEPS}
    _plan_has_cloud = any(name in _cloud_step_names for name, _ in plan)

    overall_deadline = time.time() + cfg.timeouts.total
    try:
        for name, fn in plan:
            if time.time() > overall_deadline:
                print(
                    f"e2e: total-run timeout ({cfg.timeouts.total}s) hit before "
                    f"step {name}; skipping remaining steps",
                )
                break
            print(f"e2e: ▶ {name}")
            try:
                result = fn(ctx)
            except Exception as exc:
                # Step crashed unexpectedly (didn't catch via StepRecorder).
                from .result import StepResult
                result = StepResult(
                    name=name,
                    status="failed",
                    started_at=time.time(),
                    duration_s=0.0,
                    summary="harness exception",
                    error=f"{type(exc).__name__}: {exc}",
                )
            report.steps.append(result)
            print(
                f"e2e: {STATUS_LOG_ICONS.get(result.status, '?')} {name} "
                f"[{result.status}] {result.duration_s:.1f}s — "
                f"{result.summary or result.error or ''}",
            )
            if result.status == "failed" and not args.continue_on_failure:
                print(
                    "e2e: stopping at first failure "
                    "(use --continue-on-failure to keep going)",
                )
                break
    finally:
        # Guaranteed cloud teardown: if the plan included cloud steps and a
        # project was created (cloud_project_id in state), destroy it even on
        # failure / timeout / KeyboardInterrupt.  The `cloud_delete_project`
        # step clears the key on success so we don't double-destroy.
        cloud_cfg = getattr(cfg, "cloud", None)
        if (
            _plan_has_cloud
            and cloud_cfg is not None
            and cloud_cfg.teardown_always
            and ctx.state.get("cloud_project_id") is not None
        ):
            print("e2e: ▶ cloud_teardown_finally (guaranteed cleanup)")
            teardown_result = run_cloud_teardown(ctx)
            report.steps.append(teardown_result)
            print(
                f"e2e: {STATUS_LOG_ICONS.get(teardown_result.status, '?')} "
                f"cloud_teardown_finally [{teardown_result.status}] "
                f"{teardown_result.duration_s:.1f}s — "
                f"{teardown_result.summary or teardown_result.error or ''}",
            )

    report.finished_at = time.time()
    # Always write all three report formats, even on failure / early
    # termination — the PDF is the operator's primary artifact.
    config_summary = {
        "project_name": ctx.state.get("project_name"),
        "bnk_forge_url": cfg.bnk_forge_url,
        "bf_template": cfg.bf_template,
        "bfb_image": cfg.bfb_image,
        "jumphost_host": cfg.jumphost.host if cfg.jumphost else None,
        "bnk_forge_build": ctx.state.get("bnk_forge_build") or {},
    }
    write_json_report(report, report_dir / "report.json")
    write_md_report(report, report_dir / "report.md")
    try:
        write_pdf_report(
            report, report_dir / "report.pdf",
            config_summary=config_summary,
        )
    except Exception as exc:
        # PDF generation should never block the run from reporting.
        # Log and keep going — JSON + Markdown are still on disk.
        print(f"e2e: ! PDF generation failed: {type(exc).__name__}: {exc}")
    print(
        f"e2e: ✓ done — overall={report.overall_status} "
        f"duration={report.duration_s:.1f}s — see {report_dir}/report.pdf",
    )
    return 0 if report.overall_status in ("ok", "warn") else 1


if __name__ == "__main__":
    sys.exit(main())
