"""bloodaxe_auto.py — Bloodaxe orchestrator (pre_build / open / exit_review).

Usage:
    python3 bloodaxe_auto.py pre_build
    python3 bloodaxe_auto.py open
    python3 bloodaxe_auto.py exit_review
    python3 bloodaxe_auto.py pre_build --dry-run

Phases mirror VIDAR's structure so cron wiring is uniform.

Cron schedule (paper-only on first deploy):
    0 1 * * 1-5  bloodaxe_auto.py pre_build     # 09:00 ET, before RTH open
    35 1 * * 1-5 bloodaxe_auto.py open          # 09:35 ET, just after RTH open
    */20 1-8 * * 1-5 bloodaxe_auto.py exit_review  # every 20m during RTH

Audit tag: bloodaxe.ic
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path

BLOODAXE_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, "/home/freya/RAGNAR/verticals-bot")
sys.path.insert(0, str(BLOODAXE_HOME))

from dotenv import load_dotenv
for p in (Path("/home/freya/.env"),
          Path("/home/freya/RAGNAR/verticals-bot/.env"),
          BLOODAXE_HOME / ".env"):
    if p.exists():
        load_dotenv(p)

# Audit log (shared with other strategies)
AUDIT_LOG = Path("/home/freya/RAGNAR/verticals_bot_audit.jsonl")
SPECS_DIR = BLOODAXE_HOME / "ragnar_scripts" / "ragnar_specs"

# 2026-09-25 Bloodaxe defaults
DEFAULT_MAX_LOSS_PER_TRADE_USD = 500
DEFAULT_PROFIT_TAKE_PCT = 0.50
DEFAULT_STOP_LOSS_MULTIPLE = 2.0
DEFAULT_DTE_CLOSE = 7


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


def _audit(event: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps({"strategy": "bloodaxe", "ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")


def phase_pre_build(dry_run: bool = False) -> int:
    """Run the daily scanner and emit a candidate spec."""
    _log("=== Bloodaxe pre_build ===")
    from cli.scan import scan_bloodaxe

    today = date.today()
    spec = scan_bloodaxe(today=today)

    out_path = SPECS_DIR / f"bloodaxe_{today.strftime('%Y%m%d')}.json"
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(spec, indent=2) + "\n")
        _log(f"  wrote spec -> {out_path}")
    else:
        _log(f"  DRY RUN: would write -> {out_path}")

    if spec.get("skip_reasons"):
        _log(f"  skip_reasons: {spec['skip_reasons']}")
    if spec.get("candidates"):
        _log(f"  candidates: {len(spec['candidates'])}")
        for c in spec["candidates"]:
            _log(f"    - {c['underlying']} event_window={c['event_window']}")

    _audit({"stage": "bloodaxe_pre_build", "today": today.isoformat(), "candidates": len(spec.get("candidates", []))})
    return 0


def phase_open(dry_run: bool = False) -> int:
    """Open the position from today's spec (skeleton — needs broker integration)."""
    _log("=== Bloodaxe open ===")
    today = date.today()
    spec_path = SPECS_DIR / f"bloodaxe_{today.strftime('%Y%m%d')}.json"
    if not spec_path.exists():
        _log(f"  ! no spec at {spec_path} — skipping (run pre_build first)")
        return 0

    spec = json.loads(spec_path.read_text())
    _log(f"  spec candidates: {len(spec.get('candidates', []))}")

    # The skeleton currently returns no 'picked' candidate — broker
    # integration pending. Operator can wire up phase_open to read the
    # picked candidate and submit via place_combo_iron_condor.
    _audit({
        "stage": "bloodaxe_open",
        "today": today.isoformat(),
        "status": "skeleton_no_broker_integration",
    })
    return 0


def phase_exit_review(dry_run: bool = False) -> int:
    """Evaluate exits on open Bloodaxe positions (skeleton)."""
    _log("=== Bloodaxe exit_review ===")
    _audit({"stage": "bloodaxe_exit_review", "status": "skeleton_no_broker_integration"})
    return 0


def main():
    ap = argparse.ArgumentParser(description="Bloodaxe orchestrator")
    ap.add_argument("phase", choices=["pre_build", "open", "exit_review"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.phase == "pre_build":
        return phase_pre_build(args.dry_run)
    elif args.phase == "open":
        return phase_open(args.dry_run)
    elif args.phase == "exit_review":
        return phase_exit_review(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
