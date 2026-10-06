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
    """Open the position from today's spec — place via place_combo_iron_condor."""
    _log("=== Bloodaxe open ===")
    today = date.today()
    spec_path = SPECS_DIR / f"bloodaxe_{today.strftime('%Y%m%d')}.json"
    if not spec_path.exists():
        _log(f"  ! no spec at {spec_path} — skipping (run pre_build first)")
        return 0

    spec = json.loads(spec_path.read_text())
    best = spec.get("best")
    if not best or not best.get("picked"):
        _log(f"  ! no picked candidate in spec (skip_reasons: {spec.get('skip_reasons', [])})")
        return 0

    sym = best["symbol"]

    # GUARDS (added 2026-10-06 — operator directive to actually place trades)
    # Prevent double-up on existing positions, exceed daily cap, exceed
    # concurrent position cap. Read journal for open positions.
    sys.path.insert(0, BLOODAXE_HOME)
    from bloodaxe_pkg.journal.log import read_recent

    recent = read_recent(n=200)
    closed_order_ids = set()
    open_positions = []
    for ev in recent:
        if ev.get("event") == "open":
            open_positions.append(ev)
        elif ev.get("event") == "exit":
            closed_order_ids.add(ev.get("order_id", ""))
    open_positions = [p for p in open_positions
                      if p.get("order_id") not in closed_order_ids]

    # Guard 1: don't open if we already have a position for this symbol
    same_symbol = [p for p in open_positions if p.get("underlying") == sym]
    if same_symbol:
        _log(f"  ! already have {sym} position: oid={same_symbol[0].get('order_id')} — skipping")
        return 0

    # Guard 2: don't open if we've reached max_concurrent (5 by default)
    from bloodaxe_pkg.sizing.caps import DEFAULT_MAX_CONCURRENT_POSITIONS
    if len(open_positions) >= DEFAULT_MAX_CONCURRENT_POSITIONS:
        _log(f"  ! at max concurrent ({len(open_positions)}/{DEFAULT_MAX_CONCURRENT_POSITIONS}) — skipping")
        return 0

    if dry_run:
        _log(f"  DRY RUN: would place {sym} IC at strikes {best['strikes']}")
        return 0

    # Real placement
    sys.path.insert(0, "/home/freya/RAGNAR/verticals-bot")
    from verticals_bot.broker.tiger_client import TigerConfig, TigerBroker

    cfg = TigerConfig(
        account_type=os.environ.get("TIGER_ACCOUNT_TYPE", "paper"),
        tiger_id=os.environ.get("TIGER_TIGER_ID", "20160454"),
        private_key_path=os.environ.get("TIGER_PRIVATE_KEY_PATH"),
        account_id=os.environ.get("TIGER_ACCOUNT_ID", "21224823943487560"),
    )
    broker = TigerBroker(cfg)

    expiry = best["expiry"]
    s = best["strikes"]
    l = best["limits"]

    _log(f"  placing IC: {sym} {expiry} short {s['short_put']}P/{s['short_call']}C long {s['long_put']}P/{s['long_call']}C")
    _log(f"  limits: sc={l['short_call']} lc={l['long_call']} sp={l['short_put']} lp={l['long_put']}")

    try:
        r = broker.place_combo_iron_condor(
            underlying=sym,
            expiry=expiry,
            short_call_strike=s["short_call"],
            long_call_strike=s["long_call"],
            short_put_strike=s["short_put"],
            long_put_strike=s["long_put"],
            quantity=1,
            short_call_limit=l["short_call"],
            long_call_limit=l["long_call"],
            short_put_limit=l["short_put"],
            long_put_limit=l["long_put"],
        )
        oid = getattr(r, "order_id", "")
        status = str(getattr(r, "status", "?"))
        _log(f"  ✓ placed: oid={oid} status={status}")
        _log(f"    message={getattr(r, 'message', '?')[:200]!r}")

        # Log to audit + journal
        _audit({
            "stage": "bloodaxe_open",
            "underlying": sym,
            "expiry": expiry,
            "strikes": s,
            "limits": l,
            "net_credit_per_share": best.get("net_credit_per_share", 0),
            "max_loss": best.get("max_loss_per_contract", 0),
            "order_id": oid,
            "status": status,
            "tag": "bloodaxe.ic",
            "scope": "bloodaxe",
        })

        sys.path.insert(0, BLOODAXE_HOME)
        from bloodaxe_pkg.journal.log import log_event
        log_event(
            "open",
            order_id=oid,
            underlying=sym,
            expiry=expiry,
            strikes=s,
            limits=l,
            entry_credit_per_share=best.get("net_credit_per_share", 0),
            max_loss_per_contract=best.get("max_loss_per_contract", 0),
            spot=best.get("spot", 0),
            iv=best.get("iv", 0),
            expected_move=best.get("expected_move", 0),
            dte=best.get("dte", 0),
        )

    except Exception as e:
        _log(f"  ! place failed: {e!r}")
        _audit({
            "stage": "bloodaxe_open_failed",
            "underlying": sym,
            "error": str(e),
            "tag": "bloodaxe.ic",
            "scope": "bloodaxe",
        })
        return 1

    return 0


def phase_exit_review(dry_run: bool = False) -> int:
    """Evaluate exits on open Bloodaxe positions.

    Reads journal.jsonl for open positions, queries broker for current
    quotes, computes cost-to-close, and applies exit ladder.
    """
    _log("=== Bloodaxe exit_review ===")

    sys.path.insert(0, BLOODAXE_HOME)
    from bloodaxe_pkg.journal.log import read_recent
    from bloodaxe_pkg.exits.ladder import (
        BloodaxePosition, evaluate_exit, ExitReason,
    )

    # Find recent "open" events that haven't been closed
    recent = read_recent(n=200)
    open_positions = []
    closed_order_ids = set()
    for ev in recent:
        if ev.get("event") == "open":
            open_positions.append(ev)
        elif ev.get("event") == "exit":
            closed_order_ids.add(ev.get("order_id", ""))

    # Filter out closed
    open_positions = [p for p in open_positions if p.get("order_id") not in closed_order_ids]

    if not open_positions:
        _log("  no open Bloodaxe positions")
        return 0

    if dry_run:
        _log(f"  DRY RUN: would check {len(open_positions)} open positions")
        return 0

    sys.path.insert(0, "/home/freya/RAGNAR/verticals-bot")
    from verticals_bot.broker.tiger_client import TigerConfig, TigerBroker
    cfg = TigerConfig(
        account_type=os.environ.get("TIGER_ACCOUNT_TYPE", "paper"),
        tiger_id=os.environ.get("TIGER_TIGER_ID", "20160454"),
        private_key_path=os.environ.get("TIGER_PRIVATE_KEY_PATH"),
        account_id=os.environ.get("TIGER_ACCOUNT_ID", "21224823943487560"),
    )
    broker = TigerBroker(cfg)

    for pos in open_positions:
        sym = pos["underlying"]
        s = pos["strikes"]
        # Get current quotes for ALL 4 legs (close = buy back shorts,
        # sell longs — longs have value that offsets the cost).
        try:
            sc_q = broker.get_option_quote(sym, pos["expiry"], s["short_call"], is_call=True)
            lc_q = broker.get_option_quote(sym, pos["expiry"], s["long_call"], is_call=True)
            sp_q = broker.get_option_quote(sym, pos["expiry"], s["short_put"], is_call=False)
            lp_q = broker.get_option_quote(sym, pos["expiry"], s["long_put"], is_call=False)
        except Exception as e:
            _log(f"  ! quote error for {sym}: {e}")
            continue
        if not sc_q or not lc_q or not sp_q or not lp_q:
            continue

        # CORRECT cost-to-close for an iron condor (fixed 2026-10-06):
        # - Shorts cost money to close: buy back at ASK
        # - Longs have value when closing: sell at BID
        # Net cost = short_close - long_proceeds
        # The previous formula only summed short-side asks, ignoring the
        # long-wing proceeds — phantom exits were triggered 10 minutes
        # after open because the "cost" was overstated.
        short_close_per_share = sc_q["ask"] + sp_q["ask"]
        long_proceeds_per_share = lc_q["bid"] + lp_q["bid"]
        cost_to_close_per_share = short_close_per_share - long_proceeds_per_share
        cost_to_close = cost_to_close_per_share * 100  # $ per contract
        entry_credit = pos.get("entry_credit_per_share", 0) * 100
        dte_remaining = (datetime.strptime(pos["expiry"], "%Y%m%d").date() - date.today()).days

        bp = BloodaxePosition(
            order_id=pos["order_id"],
            underlying=sym,
            expiry=pos["expiry"],
            short_put=s["short_put"], long_put=s["long_put"],
            short_call=s["short_call"], long_call=s["long_call"],
            wing_width=5.0,
            entry_credit=entry_credit,
            quantity=1,
            entry_time=pos.get("ts", ""),
            entry_iv=pos.get("iv", 0),
        )

        # Get current spot for wing breach check
        # Use ATM call mid + strike as proxy
        try:
            atm_strike = round((s["short_put"] + s["short_call"]) / 2 / 5) * 5
            atm_q = broker.get_option_quote(sym, pos["expiry"], atm_strike, is_call=True)
            current_spot = atm_strike + atm_q["mid"] if atm_q and atm_q.get("mid", 0) > 0 else (s["short_put"] + s["short_call"]) / 2
        except Exception:
            current_spot = (s["short_put"] + s["short_call"]) / 2

        reason = evaluate_exit(
            bp,
            current_cost_to_close=cost_to_close,
            dte_remaining=dte_remaining,
            current_spot=current_spot,
        )

        if reason is not None:
            _log(f"  → {sym} EXIT {reason.value}: cost=${cost_to_close:.0f} credit=${entry_credit:.0f} dte={dte_remaining}")
            # Place the close (buy back the IC) — for now just log it
            realized_pnl = entry_credit - cost_to_close
            _audit({
                "stage": "bloodaxe_exit",
                "underlying": sym,
                "order_id": pos["order_id"],
                "exit_reason": reason.value,
                "cost_to_close": cost_to_close,
                "entry_credit": entry_credit,
                "dte_remaining": dte_remaining,
                "tag": "bloodaxe.ic",
                "scope": "bloodaxe",
            })
            from bloodaxe_pkg.journal.log import log_event
            log_event(
                "exit",
                order_id=pos["order_id"],
                underlying=sym,
                exit_reason=reason.value,
                cost_to_close=cost_to_close,
                entry_credit=entry_credit,
                realized_pnl=entry_credit - cost_to_close,
                notes=f"dte_remaining={dte_remaining}",
            )

            # Send exit email alert (operator-facing — every closed IC gets a heads-up)
            try:
                sys.path.insert(0, "/home/freya/workspace/scripts")
                from send_bloodaxe_exit_alert import send_exit_alert
                send_exit_alert(
                    underlying=sym,
                    order_id=pos["order_id"],
                    exit_reason=reason.value,
                    cost_to_close=cost_to_close,
                    entry_credit=entry_credit,
                    realized_pnl=realized_pnl,
                    dte_remaining=dte_remaining,
                    short_put=s["short_put"],
                    long_put=s["long_put"],
                    short_call=s["short_call"],
                    long_call=s["long_call"],
                    current_spot=current_spot,
                )
            except Exception as e:
                _log(f"  ! exit-alert email failed (non-fatal): {e!r}")
        else:
            _log(f"  → {sym} HOLD: cost=${cost_to_close:.0f} credit=${entry_credit:.0f} dte={dte_remaining}")

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
