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


SPOT_TOLERANCE_ENV_VAR = "BLOODAXE_SPOT_TOLERANCE_PCT"
DEFAULT_SPOT_TOLERANCE_PCT = 5.0
"""Max % disagreement between ATM-call-derived spot and get_bars() latest
close. Above this, refuse the trade — protects against stale ATM_GUESSES,
underlying splits, broker data inconsistency. Configurable via env var."""


def _fetch_bars(broker, symbol: str, limit: int = 5):
    """Fetch recent daily bars for spot cross-check + skew adjustment.

    Returns a pandas DataFrame sorted ascending by time (most recent last),
    or None on failure. Caller must handle None gracefully — this function
    never raises (bars failures are non-fatal, scanner falls back to IV-only).
    """
    try:
        from tigeropen.common.consts import BarPeriod
        qc = broker._quote_client
        bars = qc.get_bars(symbol, period=BarPeriod.DAY, limit=limit)
        if bars is None or len(bars) == 0:
            return None
        return bars.sort_values("time").reset_index(drop=True)
    except Exception:
        return None


def _get_spot(broker, symbol: str, expiry: str | None = None,
              strike_step: float = 5.0, *, bars=None):
    """Get current spot price for an underlying, cross-checked against bars.

    Returns ``(spot, skip_reason, bars_used)`` tuple.
    - ``spot``: float or None
    - ``skip_reason``: None on success, str on any failure
    - ``bars_used``: DataFrame or None (passed through for reuse by skew calc)

    Strategy:
    1. Try get_briefs (Tiger paper doesn't expose this — falls back).
    2. Infer from ATM call mid (call_mid ≈ spot - strike for ATM-ish).
    3. **Cross-check vs get_bars() latest close** (2026-10-06, BABA spot
       bug fix). If disagreement > ``BLOODAXE_SPOT_TOLERANCE_PCT`` (default
       5%), refuse — return ``spot_disagrees_with_bars_<pct>pct``. Catches
       stale ATM_GUESSES, splits, broker data inconsistency.

    If ``bars`` is supplied (e.g. from a prior fetch in the same scan), we
    skip the second get_bars call. If bars fetch fails, we FAIL SAFE
    (return skip_reason="spot_validation_failed") — no spot is better than
    a wrong spot.
    """
    # Step 1: try briefs
    spot = None
    try:
        tc = broker._trade_client
        briefs = tc.get_briefs([symbol]) if hasattr(tc, "get_briefs") else None
        if briefs and symbol in briefs:
            last = float(briefs[symbol].get("last", 0) or 0)
            if last > 0:
                spot = last
    except Exception:
        pass

    # Step 2: derive from ATM call mid if briefs failed
    if spot is None or spot <= 0:
        if not expiry:
            today = date.today()
            next_exp = _next_expiry(today, weekly=False)
            if next_exp:
                spot = _infer_spot_from_atm_call(
                    broker, symbol, next_exp.strftime("%Y%m%d"), strike_step
                )
        else:
            spot = _infer_spot_from_atm_call(broker, symbol, expiry, strike_step)

    if spot is None or spot <= 0:
        return None, "no_spot_quote", bars

    # Step 3: cross-check against get_bars() latest close
    if bars is None:
        bars = _fetch_bars(broker, symbol, limit=5)

    if bars is None or len(bars) == 0:
        # Fail safe — no bar data means we can't validate spot. Better to
        # skip than trade on a possibly-wrong spot.
        return None, "spot_validation_no_bars", bars

    try:
        bar_close = float(bars["close"].iloc[-1])
    except Exception:
        return None, "spot_validation_bad_bar", bars

    if bar_close <= 0:
        return None, "spot_validation_zero_bar", bars

    pct_diff = abs(spot - bar_close) / bar_close * 100.0
    try:
        tolerance = float(os.environ.get(SPOT_TOLERANCE_ENV_VAR,
                                            str(DEFAULT_SPOT_TOLERANCE_PCT)))
    except ValueError:
        tolerance = DEFAULT_SPOT_TOLERANCE_PCT

    if pct_diff > tolerance:
        return None, f"spot_disagrees_with_bars_{pct_diff:.1f}pct", bars

    return spot, None, bars


def _infer_spot_from_atm_call(broker, symbol: str, expiry: str,
                               strike_step: float) -> float | None:
    """Find spot via multi-strike averaging across calls AND puts.

    Old behavior (single-strike call derivation) was vulnerable to
    illiquid single-name quotes (NVDA/AMD/BABA disagreements on
    2026-10-06). New approach:

    1. Walk 3 strikes around the ATM guess: (center-step, center, center+step)
    2. For each strike, get BOTH the call and put quotes
    3. Convert each quote to a spot estimate:
       - Call: spot ≈ strike + call_mid (intrinsic + time value)
       - Put:  spot ≈ strike - put_mid  (intrinsic + time value)
    4. Return the MEDIAN of valid estimates

    Median is robust to one bad quote among 6 (2 sides × 3 strikes).
    The bars cross-check in _get_spot still catches systemic disagreement.

    Cost: 6 option quotes per symbol vs 1 previously. Tiger paper rate-limit
    budget is ~60-120 quotes/min; with 6 symbols × 6 quotes = 36 calls
    per scan cycle, well within budget.
    """
    # Symbol-specific ATM strike guesses (refresh quarterly). Calibrated
    # against Tiger paper latest close on 2026-10-06:
    #   SPY=$774.83, QQQ=$756.20, IWM=$283.38, NVDA=$238.90,
    #   AMD=$631.75, BABA=$110.80. The cross-check in _get_spot catches
    # disagreement > BLOODAXE_SPOT_TOLERANCE_PCT (default 5%), so these
    # guesses can drift between quarterly refreshes.
    ATM_GUESSES = {
        "SPY": 775, "QQQ": 755, "IWM": 285,
        "NVDA": 240, "AMD": 630, "BABA": 110,
        "MSFT": 415, "META": 555, "AMZN": 195, "GOOG": 175,
    }
    guess = ATM_GUESSES.get(symbol, 100)
    atm_center = round(guess / strike_step) * strike_step

    estimates: list[float] = []
    for offset in (-strike_step, 0, strike_step):
        strike = atm_center + offset
        # Try call first
        q = broker.get_option_quote(symbol, expiry, strike, is_call=True)
        if q and q.get("mid", 0) > 0:
            spot_est = strike + q["mid"]
            if 50 < spot_est < 5000:
                estimates.append(spot_est)
        # Cross-check with put (independent quote source)
        q = broker.get_option_quote(symbol, expiry, strike, is_call=False)
        if q and q.get("mid", 0) > 0:
            spot_est = strike - q["mid"]
            if 50 < spot_est < 5000:
                estimates.append(spot_est)

    if not estimates:
        return None

    # Median is robust to 1-2 bad quotes among 6 total estimates
    estimates.sort()
    n = len(estimates)
    if n % 2 == 1:
        return float(estimates[n // 2])
    # Average the two middle for even-length lists
    return float((estimates[n // 2 - 1] + estimates[n // 2]) / 2)


def _check_chain_depth(
    broker, symbol: str, expiry: str,
    target_short_call: float, target_short_put: float,
    strike_step: float = 5.0, search_range: int = 2,
) -> tuple[bool, str | None]:
    """Verify the option chain has data in the expected range for an IC.

    Probes ±``search_range`` strikes (default ±2 = 5 strikes per side) around
    each target short strike. If EITHER side returns zero quoted strikes
    (mid > 0), the chain is fundamentally sparse for this underlying and
    we should skip immediately rather than waste 12+ quote calls in the
    strike-pair fallback.

    Returns ``(ok, skip_reason)``:
    - ``ok=True, skip_reason=None`` → chain has data, proceed with fallback
    - ``ok=False, skip_reason="chain_sparse_calls_at_<X>"`` → skip cleanly

    Cost: 10 quotes per scan (5 calls × 2 sides). Tiger paper budget is
    ~60-120 quotes/min, so 60 quotes/scan for 6 symbols is comfortable.

    Discovery: AMD on Tiger paper has spot=$632, IV=53%, EM=$117 → correct
    strikes SP=515/SC=750, but chain only has data for strikes $220-270
    (stale snapshot from when AMD was cheaper). 2026-10-06.
    """
    call_quoted = 0
    for offset in range(-search_range, search_range + 1):
        strike = target_short_call + (offset * strike_step)
        q = broker.get_option_quote(symbol, expiry, strike, is_call=True)
        if q and q.get("mid", 0) > 0:
            call_quoted += 1
    if call_quoted == 0:
        return False, f"chain_sparse_calls_no_quotes_near_{target_short_call:.0f}"

    put_quoted = 0
    for offset in range(-search_range, search_range + 1):
        strike = target_short_put + (offset * strike_step)
        q = broker.get_option_quote(symbol, expiry, strike, is_call=False)
        if q and q.get("mid", 0) > 0:
            put_quoted += 1
    if put_quoted == 0:
        return False, f"chain_sparse_puts_no_quotes_near_{target_short_put:.0f}"

    return True, None


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

    # _get_spot returns (spot, skip_reason, bars_used). We reuse the bars
    # for the skew-adjustment calc below to avoid a second get_bars call.
    spot, spot_skip_reason, bars_for_skew = _get_spot(broker, sym)
    if spot is None or spot <= 0:
        return {"symbol": sym, "skip_reason": spot_skip_reason or "no_spot_quote"}

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
    # Reuse bars from the cross-check rather than fetching fresh.
    hv_value = None
    dhv_value = None
    skew_ratio = None
    em_put_adjusted = None
    em_call_adjusted = None
    bars_available = False
    try:
        # If we only fetched 5 bars for spot validation, get a wider window
        # for HV. Cheap (same broker endpoint, same auth).
        bars = bars_for_skew
        if bars is not None and len(bars) >= 5 and len(bars) < 31:
            bars = _fetch_bars(broker, sym, limit=60)
        if bars is not None and len(bars) >= 31:
            closes = bars["close"].dropna().tolist()
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

    # --- Chain-depth pre-check (added 2026-10-06, AMD discovery) ---
    # Probe ±2 strikes around each target to verify the chain has data in
    # the expected range. If either side has ZERO quoted strikes, the chain
    # is fundamentally sparse and we skip immediately with a clear,
    # operator-readable reason. Saves the 12+ quote calls that the
    # strike-pair fallback would otherwise waste on a hopeless chain.
    chain_ok, skip_chain_reason = _check_chain_depth(
        broker, sym, expiry,
        target_short_call=short_call,
        target_short_put=short_put,
        strike_step=strike_step,
    )
    if not chain_ok:
        return {"symbol": sym, "skip_reason": skip_chain_reason}

    # Get quotes for all 4 legs. If target strike has no quote, walk inward
    # toward spot (up to MAX_STRIKE_FALLBACK steps) to find quoted strikes.
    # Keeps wing_width intact — both legs of a side shift together.
    MAX_STRIKE_FALLBACK = 3

    def _find_leg_pair(target_short, is_call):
        """Walk inward from target_short until both legs (target, target+wing)
        have valid quotes. Returns (short_strike, long_strike, short_q, long_q)
        or (None, None, None, None) on failure."""
        direction = -1 if is_call else 1  # calls walk down, puts walk up
        for step in range(MAX_STRIKE_FALLBACK + 1):
            sc = target_short + (step * direction * strike_step)
            lc = sc + wing_width if is_call else sc - wing_width
            sc_q = broker.get_option_quote(sym, expiry, sc, is_call=is_call)
            lc_q = broker.get_option_quote(sym, expiry, lc, is_call=is_call)
            if (sc_q and sc_q.get("mid", 0) > 0 and
                    lc_q and lc_q.get("mid", 0) > 0):
                return sc, lc, sc_q, lc_q
        return None, None, None, None

    sc, lc, sc_q, lc_q = _find_leg_pair(short_call, is_call=True)
    if sc is None:
        return {
            "symbol": sym,
            "skip_reason": f"no_quote_for_call_pair_near_{short_call}"
        }
    short_call, long_call = sc, lc

    sp, lp, sp_q, lp_q = _find_leg_pair(short_put, is_call=False)
    if sp is None:
        return {
            "symbol": sym,
            "skip_reason": f"no_quote_for_put_pair_near_{short_put}"
        }
    short_put, long_put = sp, lp

    # All 4 legs have valid quotes — assemble the legs dict
    legs = {}
    for leg_name, q, strike in [
        ("short_call", sc_q, short_call),
        ("long_call", lc_q, long_call),
        ("short_put", sp_q, short_put),
        ("long_put", lp_q, long_put),
    ]:
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
