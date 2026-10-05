"""Bloodaxe daily scanner — multi-underlying, event-aware, broker-integrated.

Pipeline:
1. For each candidate in the universe, check event screen (Rule 2)
2. Get spot + ATM IV from Tiger broker (Rule 1 + Rule 3)
3. Compute expected move (Rule 3)
4. Pick strikes outside EM (Rules 4-5)
5. Get quotes for all 4 legs, compute mid prices
6. Apply $500/condor sizing (Rules 6, 8)
7. Emit a "picked" candidate spec with strikes + expected credit

Output: JSON spec (stdout or --out PATH). The phase_open orchestrator
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


# ---------------------------------------------------------------------------
# Default Bloodaxe universe
# ---------------------------------------------------------------------------

DEFAULT_UNIVERSE = [
    # ETFs (5pt wings, $5 strike step)
    {"symbol": "SPY",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 2},
    {"symbol": "QQQ",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "IWM",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    # Single names (5pt wings)
    {"symbol": "NVDA", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "AMD",  "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
    {"symbol": "BABA", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_expiry(today: date, min_dte: int = 30, max_dte: int = 45,
                 weekly: bool = False) -> date | None:
    """Pick the next available expiry >= min_dte days from today.

    Walks forward and returns the first monthly (or weekly if requested)
    that's >= min_dte days away. Allows expiry beyond max_dte if needed.

    Returns None if no expiry found within 90 days.
    """
    cursor = today + timedelta(days=min_dte)
    end = today + timedelta(days=120)
    while cursor <= end:
        # For monthlies: target 3rd Friday of month
        first_day = cursor.replace(day=1)
        first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
        third_friday = first_friday + timedelta(days=14)
        if third_friday >= cursor:
            return third_friday
        # Move to next month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return None


def _round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 2)


def _round_leg_tick(price: float, underlying: str) -> float:
    """Per-leg tick rounding — SPY/QQQ/IWM use 0.05 sub-$3, 0.10 above; stocks use 0.01."""
    if underlying in ("SPY", "QQQ", "IWM"):
        tick = 0.10 if price >= 3.0 else 0.05
    else:
        tick = 0.01
    return _round_tick(price, tick)


# ---------------------------------------------------------------------------
# Broker integration
# ---------------------------------------------------------------------------

def _get_broker():
    """Lazy import + construct TigerBroker using paper account."""
    try:
        from verticals_bot.broker.tiger_client import TigerConfig, TigerBroker
    except ImportError:
        return None
    cfg = TigerConfig(
        account_type=os.environ.get("TIGER_ACCOUNT_TYPE", "paper"),
        tiger_id=os.environ.get("TIGER_TIGER_ID", "20160454"),
        private_key_path=os.environ.get(
            "TIGER_PRIVATE_KEY_PATH",
            "/home/freya/RAGNAR/tiger_openapi_demo.properties",
        ),
        account_id=os.environ.get("TIGER_ACCOUNT_ID", "21224823943487560"),
    )
    return TigerBroker(cfg)


def _get_spot(broker, symbol: str, expiry: str | None = None,
              strike_step: float = 5.0) -> float | None:
    """Get current spot price for an underlying.

    Strategy:
    1. Try get_briefs (Tiger paper doesn't expose this — falls back).
    2. Infer from ATM call mid (call_mid ≈ spot - strike for ATM-ish).
       Faster than full straddle parity, slightly less accurate.
    """
    # Try briefs first
    try:
        tc = broker._trade_client
        briefs = tc.get_briefs([symbol]) if hasattr(tc, "get_briefs") else None
        if briefs and symbol in briefs:
            last = float(briefs[symbol].get("last", 0) or 0)
            if last > 0:
                return last
    except Exception:
        pass

    # Fallback: derive spot from ATM call at the next monthly expiry
    if not expiry:
        today = date.today()
        next_exp = _next_expiry(today, weekly=False)
        if next_exp:
            spot = _infer_spot_from_atm_call(broker, symbol, next_exp.strftime("%Y%m%d"), strike_step)
            if spot:
                return spot
        return None

    return _infer_spot_from_atm_call(broker, symbol, expiry, strike_step)


def _infer_spot_from_atm_call(broker, symbol: str, expiry: str,
                               strike_step: float) -> float | None:
    """Find ATM strike, estimate spot as strike + ATM call mid.

    Uses symbol-specific ATM guesses (calibrated for current price
    levels — operator can refresh quarterly). Skips the guess loop
    to keep quote lookups minimal (Tiger paper rate limit).
    """
    # Symbol-specific ATM strike guesses (refresh quarterly)
    ATM_GUESSES = {
        "SPY": 770, "QQQ": 485, "IWM": 227,
        "NVDA": 245, "AMD": 155, "BABA": 180,
        "MSFT": 415, "META": 555, "AMZN": 195, "GOOG": 175,
    }
    guess = ATM_GUESSES.get(symbol, 100)
    atm = round(guess / strike_step) * strike_step
    # Just check the ATM strike (skip offset loop for speed)
    q = broker.get_option_quote(symbol, expiry, atm, is_call=True)
    if q and q.get("mid", 0) > 0:
        spot_estimate = atm + q["mid"]
        if 50 < spot_estimate < 2000:
            return float(spot_estimate)
    # Fallback: try ±1 strike in case ATM is off
    for offset in [-strike_step, strike_step]:
        q = broker.get_option_quote(symbol, expiry, atm + offset, is_call=True)
        if q and q.get("mid", 0) > 0:
            spot_estimate = (atm + offset) + q["mid"]
            if 50 < spot_estimate < 2000:
                return float(spot_estimate)
    return None


def _get_atm_iv(broker, symbol: str, spot: float, expiry: str,
                strike_step: float = 5.0) -> float | None:
    """Get IV from the ATM option (closest strike to spot)."""
    atm_strike = round(spot / strike_step) * strike_step
    q = broker.get_option_quote(symbol, expiry, atm_strike, is_call=True)
    if q and q.get("iv"):
        return float(q["iv"])
    # Fall back to ATM put
    q = broker.get_option_quote(symbol, expiry, atm_strike, is_call=False)
    if q and q.get("iv"):
        return float(q["iv"])
    return None


def _select_pick(underlying_cfg: dict, broker, today: date) -> dict | None:
    """Build a candidate pick for one underlying.

    Returns a dict with strikes, expiry, expected move, net credit
    estimate, etc. None if any step fails.
    """
    sym = underlying_cfg["symbol"]
    wing_width = underlying_cfg["wing_width"]
    strike_step = underlying_cfg["strike_step"]

    spot = _get_spot(broker, sym)
    if not spot or spot <= 0:
        return {"symbol": sym, "skip_reason": "no_spot_quote"}

    expiry_date = _next_expiry(today, weekly=False)  # monthlies
    if not expiry_date:
        return {"symbol": sym, "skip_reason": "no_expiry_in_window"}
    expiry = expiry_date.strftime("%Y%m%d")
    dte = (expiry_date - today).days

    iv = _get_atm_iv(broker, sym, spot, expiry, strike_step)
    if not iv or iv <= 0:
        # Fallback to a reasonable IV estimate for liquid underlyings
        # (calibrated from recent Tiger paper data: SPY ~22%, QQQ ~26%,
        # IWM ~28%, single names 35-50%)
        fallback_iv = {
            "SPY": 0.22, "QQQ": 0.26, "IWM": 0.28,
            "NVDA": 0.45, "AMD": 0.50, "BABA": 0.45,
        }.get(sym, 0.35)
        iv = fallback_iv

    # Expected move
    em = spot * iv * (dte / 365.0) ** 0.5

    # --- Realized-vol + downside-skew adjustment (Rule 3.5) ---
    # Pull ~60 trading days so a 30-day window is robust even with short gaps.
    hv_value = None
    dhv_value = None
    skew_ratio = None
    em_put_adjusted = None
    em_call_adjusted = None
    bars_available = False
    try:
        from tigeropen.common.consts import BarPeriod
        qc = broker._quote_client
        bars = qc.get_bars(sym, period=BarPeriod.DAY, limit=60)
        if bars is not None and len(bars) >= 31:
            closes = bars.sort_values("time")["close"].dropna().tolist()
            from bloodaxe_pkg.expected_move.realized_vol import (
                compute_hv,
                compute_downside_hv,
                compute_downside_skew_ratio,
                skew_adjusted_em,
                adjusted_call_em,
            )
            hv_value = compute_hv(closes, window=30)
            dhv_value = compute_downside_hv(closes, window=30)
            skew_ratio = compute_downside_skew_ratio(closes, window=30)
            em_put_adjusted = skew_adjusted_em(
                spot=spot, iv=iv, closes=closes, dte=dte, skew_safety=1.0
            )
            em_call_adjusted = adjusted_call_em(
                spot=spot, iv=iv, closes=closes, dte=dte
            )
            bars_available = True
    except Exception:
        # Graceful fallback — leave values None, strike picker uses IV-only.
        pass

    # Resolve effective per-side EMs (Rule 3.5) with symmetric fallback.
    em_put_used = em_put_adjusted if (bars_available and em_put_adjusted) else em
    em_call_used = em_call_adjusted if (bars_available and em_call_adjusted) else em

    short_call_target = spot + em_call_used
    short_put_target = spot - em_put_used
    upper = round(short_call_target) // strike_step * strike_step
    lower = round(short_put_target) // strike_step * strike_step
    short_call = upper
    long_call = upper + wing_width
    short_put = lower
    long_put = lower - wing_width

    # Get quotes for all 4 legs
    legs = {}
    for leg_name, is_call, strike in [
        ("short_call", True, short_call),
        ("long_call", True, long_call),
        ("short_put", False, short_put),
        ("long_put", False, long_put),
    ]:
        q = broker.get_option_quote(sym, expiry, strike, is_call=is_call)
        if not q or q.get("mid", 0) <= 0:
            return {"symbol": sym, "skip_reason": f"no_quote_for_{leg_name}_{strike}"}
        # Apply 1.02 × mid markup with proper tick rounding
        if is_call:
            lim = _round_leg_tick(q["mid"] * 1.02, sym)
        else:
            lim = _round_leg_tick(q["mid"] * 1.02, sym)
        legs[leg_name] = {"mid": q["mid"], "limit": lim, "iv": q.get("iv", 0),
                          "strike": strike, "expiry": expiry}

    # Compute net credit
    sc_lim = legs["short_call"]["limit"]
    lc_lim = legs["long_call"]["limit"]
    sp_lim = legs["short_put"]["limit"]
    lp_lim = legs["long_put"]["limit"]
    net_credit_per_share = sc_lim + sp_lim - lc_lim - lp_lim
    max_loss_per_contract = wing_width * 100 - net_credit_per_share * 100

    # Sizing (default $500 cap)
    from bloodaxe_pkg.sizing.caps import size_bloodaxe_ic
    sz = size_bloodaxe_ic(wing_width=wing_width)

    return {
        "symbol": sym,
        "spot": spot,
        "iv": iv,
        "dte": dte,
        "expiry": expiry,
        "expiry_date": expiry_date.isoformat(),
        "expected_move": em,
        # Rule 3.5 skew-adjustment diagnostics — operator-facing.
        # ``bars_available`` False means the scanner fell back to IV-only.
        "skew_adjustment": {
            "bars_available": bars_available,
            "hv_30d": hv_value,
            "downside_hv_30d": dhv_value,
            "skew_ratio": skew_ratio,
            "em_iv": em,
            "em_put_used": em_put_used,
            "em_call_used": em_call_used,
            "em_put_adjusted": em_put_adjusted,
            "em_call_adjusted": em_call_adjusted,
        },
        "strikes": {
            "short_put": short_put, "long_put": long_put,
            "short_call": short_call, "long_call": long_call,
        },
        "wing_width": wing_width,
        "limits": {
            "short_call": sc_lim, "long_call": lc_lim,
            "short_put": sp_lim, "long_put": lp_lim,
        },
        "net_credit_per_share": net_credit_per_share,
        "max_loss_per_contract": max_loss_per_contract,
        "sizing": sz.to_dict(),
        "picked": sz.passes_caps,
    }


def scan_bloodaxe(
    today: date | None = None,
    dte_min: int = 30,
    dte_max: int = 45,
    universe: list[dict] | None = None,
    broker=None,
) -> dict:
    """Scan + pick a Bloodaxe candidate for today."""
    today = today or date.today()
    universe = universe or DEFAULT_UNIVERSE

    from bloodaxe_pkg.scanner.events import passes_event_screen, next_event_window

    if broker is None:
        broker = _get_broker()

    skip_reasons = []
    if broker is None:
        skip_reasons.append("broker_unavailable")

    candidates = []
    for u in universe:
        candidate = {
            "underlying": u["symbol"],
            "wing_width": u["wing_width"],
            "strike_step": u["strike_step"],
            "max_open": u["max_open"],
            "event_window": next_event_window(today, earnings_date=None),
            "passes_events": passes_event_screen(today, earnings_date=None),
            "picked": False,
        }
        if not candidate["passes_events"]:
            candidate["skip_reason"] = "fails_event_screen"
        elif broker is not None:
            # Try to build a real pick
            pick = _select_pick(u, broker, today)
            if pick and pick.get("picked"):
                candidate.update(pick)
                candidate["picked"] = True
            else:
                candidate["skip_reason"] = pick.get("skip_reason", "pick_failed") if pick else "no_broker"
        else:
            candidate["skip_reason"] = "no_broker"
        candidates.append(candidate)

    # Pick the best candidate (highest credit / lowest max_loss ratio)
    picked = [c for c in candidates if c.get("picked")]
    best = None
    if picked:
        best = max(picked, key=lambda c: c.get("net_credit_per_share", 0))

    spec = {
        "as_of": datetime.now().isoformat(),
        "phase": "pre_build",
        "strategy": "bloodaxe",
        "today": today.isoformat(),
        "dte_range": [dte_min, dte_max],
        "skip_reasons": skip_reasons,
        "candidates": candidates,
        "best": best,
        "picked": bool(best),
    }
    return spec


def main():
    ap = argparse.ArgumentParser(description="Bloodaxe daily scanner")
    ap.add_argument("--dry-run", action="store_true", help="Print spec, don't write")
    ap.add_argument("--out", type=str, default=None, help="Output path")
    args = ap.parse_args()

    spec = scan_bloodaxe()
    out = json.dumps(spec, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out + "\n")
        print(f"wrote {args.out}")
        if spec.get("best"):
            b = spec["best"]
            print(f"  best: {b['symbol']} sc={b['strikes']['short_call']} sp={b['strikes']['short_put']} credit=${b['net_credit_per_share']:.2f}/share")
    else:
        print(out)


if __name__ == "__main__":
    main()
