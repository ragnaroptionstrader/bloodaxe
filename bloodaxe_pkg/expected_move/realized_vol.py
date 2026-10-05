"""Bloodaxe realized-vol + downside-skew adjustment (Rule 3.5).

Why this exists
---------------
The base expected-move model (``expected_move_from_iv``) assumes a symmetric
return distribution priced by ATM IV. Reality for many underlyings — especially
single names with fundamental/news catalysts — has two problems:

1. **Realized vol differs from implied vol.** IV is a forward-looking estimate.
   When the realized 30-day vol is materially higher than ATM IV, the
   expected_move band is too tight and short strikes sit closer to the
   realized tail than the model assumes.

2. **Returns are negatively skewed.** Left-tail moves (sell-offs) are larger
   and more frequent than right-tail moves of the same magnitude. Selling
   a "symmetric" iron condor at ±1σ ignores that the put side carries
   materially more risk than the call side.

Combined, these two effects explain BABA-style blow-ups where the short put
sits inside the realized 1σ band while IV-based math says it's safely outside.

The fix
-------
- Pull ~30 trading days of closes via Tiger ``get_bars``.
- Compute 30-day HV (annualized, log-return stdev × √252).
- Compute downside HV (annualized stdev of *negative* log returns only, scaled
  to total obs — this is the "semi-deviation" / Sortino-style downside vol).
- Build a downside-skew-adjusted expected move for the put side:

      em_put_dollar = spot × max(downside_hv, iv) × √(dte/365) × skew_safety

  ``em_call_dollar`` stays IV-based unless total HV > IV (rare for upside).
- ``skew_safety`` defaults to 1.0 (no extra widening); operator can raise to
  1.25 for additional tail-risk margin on a single name.

When bars are unavailable (rate limit, holiday, missing data), every helper
returns ``None`` and the scanner falls back to pure IV-based selection —
preserving today's behavior, never worse.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Optional


TRADING_DAYS_PER_YEAR = 252


def compute_hv(closes: Iterable[float], window: int = 30) -> Optional[float]:
    """Annualized historical volatility from a series of daily closes.

    Uses log returns: r_t = ln(P_t / P_{t-1}). Annualizes by √252.
    Returns None if fewer than ``window + 1`` closes supplied or inputs invalid.
    """
    closes = list(closes)
    if len(closes) < window + 1:
        return None
    # Use only the most recent `window` closes (need `window` log returns).
    recent = closes[-(window + 1):]
    if any(c is None or c <= 0 for c in recent):
        return None
    log_returns = [math.log(recent[i] / recent[i - 1])
                   for i in range(1, len(recent))]
    if len(log_returns) < 2:
        return None
    try:
        return statistics.stdev(log_returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    except statistics.StatisticsError:
        return None


def compute_downside_hv(closes: Iterable[float], window: int = 30) -> Optional[float]:
    """Annualized *downside* semi-deviation.

    Standard deviation of the *negative* log returns only, scaled so the
    variance uses the full ``window`` observations in the denominator
    (matches Sortino-style downside deviation). Zero-out positive returns,
    then square and average across the full window.

    Returns None if inputs invalid.
    """
    closes = list(closes)
    if len(closes) < window + 1:
        return None
    recent = closes[-(window + 1):]
    if any(c is None or c <= 0 for c in recent):
        return None
    log_returns = [math.log(recent[i] / recent[i - 1])
                   for i in range(1, len(recent))]
    n = len(log_returns)
    if n < 2:
        return None
    # Downside variance: square negative returns, average over full n.
    downside_sq = sum(min(r, 0.0) ** 2 for r in log_returns) / (n - 1)
    if downside_sq <= 0:
        return 0.0  # No down moves — fully benign
    return math.sqrt(downside_sq) * math.sqrt(TRADING_DAYS_PER_YEAR)


def compute_downside_skew_ratio(closes: Iterable[float], window: int = 30) -> Optional[float]:
    """Downside HV / total HV. >1 means the left tail is fatter than the right.

    Useful diagnostic — exposed in the spec for the operator to see.
    """
    hv = compute_hv(closes, window=window)
    dhv = compute_downside_hv(closes, window=window)
    if hv is None or dhv is None or hv <= 0:
        return None
    return dhv / hv


def skew_adjusted_em(
    spot: float,
    iv: float,
    closes: Iterable[float],
    dte: int,
    *,
    window: int = 30,
    skew_safety: float = 1.0,
) -> Optional[float]:
    """Compute the downside-skew-adjusted expected move for the PUT side.

    Returns ``spot × max(downside_hv, iv) × √(dte/365) × skew_safety``.

    Args:
        spot: current underlying price (dollars).
        iv: ATM IV as decimal (e.g. 0.29 for 29%).
        closes: ascending-ordered daily close prices (most recent last).
        dte: days to expiry.
        window: number of trading days for HV calc (default 30).
        skew_safety: extra widening multiplier (default 1.0). Operator
            can raise this (e.g. 1.25) for additional tail-risk margin
            on a single name. Must be >= 1.0.

    Returns:
        Adjusted EM in dollars, or None if data is insufficient.

    Why "max(downside_hv, iv)":
        If IV already prices the left tail (downside HV < IV), the IV-based
        band is already conservative on the put side — don't make it looser.
        Only widen when realized downside vol EXCEEDS what IV implied.
    """
    if spot <= 0 or iv <= 0 or dte <= 0:
        return None
    if skew_safety < 1.0:
        raise ValueError(f"skew_safety must be >= 1.0, got {skew_safety}")
    dhv = compute_downside_hv(closes, window=window)
    if dhv is None:
        return None
    vol_for_put = max(dhv, iv)
    return spot * vol_for_put * math.sqrt(dte / 365.0) * skew_safety


def adjusted_call_em(
    spot: float,
    iv: float,
    closes: Iterable[float],
    dte: int,
    *,
    window: int = 30,
) -> Optional[float]:
    """Compute the upside expected move for the CALL side.

    Uses max(total HV, IV) so the upside band widens when realized vol
    exceeds implied (e.g. after a big rally). Most names have upside HV
    <= IV, so this typically equals IV-based EM.

    Returns None if data is insufficient.
    """
    if spot <= 0 or iv <= 0 or dte <= 0:
        return None
    hv = compute_hv(closes, window=window)
    if hv is None:
        return None
    vol_for_call = max(hv, iv)
    return spot * vol_for_call * math.sqrt(dte / 365.0)