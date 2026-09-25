"""Bloodaxe daily scanner — multi-underlying, event-aware.

Pipeline:
1. Build candidate universe (default: liquid single names + liquid ETFs)
2. Filter by liquidity (Rule 1) and event screen (Rule 2)
3. For each candidate, compute expected move (Rule 3)
4. Pick strikes outside EM (Rule 4-5)
5. Size to $500/condor (Rule 6, 8)
6. Pre-define exits (Rule 7)
7. Emit one candidate spec per day

Output: JSON to stdout (or --out PATH). The phase_open orchestrator
reads the JSON and submits via place_combo_iron_condor.

Usage:
    python3 /home/freya/bloodaxe/cli/scan.py
    python3 /home/freya/bloodaxe/cli/scan.py --dry-run
    python3 /home/freya/bloodaxe/cli/scan.py --out /path/to/spec.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/home/freya/RAGNAR/verticals-bot")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
for p in (Path("/home/freya/.env"),
          Path("/home/freya/RAGNAR/verticals-bot/.env"),
          Path(__file__).resolve().parent.parent.parent / ".env"):
    if p.exists():
        load_dotenv(p)


# Default Bloodaxe universe: liquid ETFs + liquid single names
DEFAULT_UNIVERSE = [
    # ETFs (5pt wings)
    {"symbol": "SPY",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 2},
    {"symbol": "QQQ",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "IWM",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    # Single names (5pt wings)
    {"symbol": "NVDA", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "AMD",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "BABA", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
]


def scan_bloodaxe(
    today: date | None = None,
    dte_min: int = 30,
    dte_max: int = 45,
    universe: list[dict] | None = None,
) -> dict:
    """Scan + pick a Bloodaxe candidate for today.

    Returns:
        dict spec with picked candidate or skip_reasons.

    Note: This is the structural skeleton — it would need broker
    integration (TigerBroker) to fetch spot prices and IV, and earnings
    calendar API integration. The current implementation returns a
    "skeleton" spec the operator can wire up.
    """
    today = today or date.today()
    universe = universe or DEFAULT_UNIVERSE

    # Step 1-2: filter universe by event screen
    from bloodaxe_pkg.scanner.events import passes_event_screen, next_event_window

    candidates = []
    for u in universe:
        # Earnings date would come from a calendar API; pass None for now
        event_window = next_event_window(today, earnings_date=None)
        if not passes_event_screen(today, earnings_date=None):
            continue
        candidates.append({
            **u,
            "event_window": event_window,
        })

    # Step 3-5: pick best candidate (placeholder — needs broker data)
    spec = {
        "as_of": datetime.now().isoformat(),
        "phase": "pre_build",
        "strategy": "bloodaxe",
        "today": today.isoformat(),
        "dte_range": [dte_min, dte_max],
        "skip_reasons": [
            "broker integration pending — spot/IV fetch not implemented",
        ],
        "candidates": [
            {
                "underlying": c["symbol"],
                "wing_width": c["wing_width"],
                "strike_step": c["strike_step"],
                "max_open": c["max_open"],
                "event_window": c["event_window"],
                "picked": False,
            }
            for c in candidates
        ],
    }

    return spec


def main():
    ap = argparse.ArgumentParser(description="Bloodaxe daily scanner")
    ap.add_argument("--dry-run", action="store_true", help="Print spec, don't write")
    ap.add_argument("--out", type=str, default=None,
                    help="Output path (default: stdout)")
    args = ap.parse_args()

    spec = scan_bloodaxe()
    out = json.dumps(spec, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out + "\n")
        print(f"wrote {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
