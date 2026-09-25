"""Bloodaxe liquidity filter — Rule 1.

Validates an underlying is tradeable based on:
- Option chain exists (>=5 strikes each side of ATM)
- Tight bid/ask spreads (<10% on ATM-ish strikes)
- Volume > 0 (or open interest > 100 for illiquid names)

Returns True if the underlying passes all liquidity gates.
"""
from __future__ import annotations

from typing import Iterable


def passes_liquidity(
    chain_quotes: Iterable[dict],
    *,
    min_strikes_per_side: int = 5,
    max_spread_pct: float = 0.10,
    min_oi_or_volume: int = 100,
) -> bool:
    """Check if an underlying's option chain is liquid enough to trade.

    Args:
        chain_quotes: iterable of quote dicts with keys
            {bid, ask, mid, strike, right, open_interest, volume}.
        min_strikes_per_side: minimum number of strikes with quotes
            above + below the median strike (ATM proxy).
        max_spread_pct: max bid/ask spread as % of mid for ATM-ish
            strikes (<=20% OTM).
        min_oi_or_volume: minimum open interest OR volume required.

    Returns:
        True if all liquidity gates pass.
    """
    quotes = list(chain_quotes)
    if len(quotes) < min_strikes_per_side * 2:
        return False

    strikes = sorted({q["strike"] for q in quotes})
    if len(strikes) < min_strikes_per_side * 2:
        return False

    median_strike = strikes[len(strikes) // 2]
    atm_window = [
        q for q in quotes
        if abs(q["strike"] - median_strike) / median_strike <= 0.20
    ]
    if len(atm_window) < min_strikes_per_side:
        return False

    for q in atm_window:
        if q["mid"] <= 0:
            continue  # skip zero-bid quotes
        spread_pct = (q["ask"] - q["bid"]) / q["mid"] if q["mid"] > 0 else 1.0
        if spread_pct > max_spread_pct:
            return False
        oi = q.get("open_interest", 0) or 0
        vol = q.get("volume", 0) or 0
        if oi < min_oi_or_volume and vol < min_oi_or_volume:
            return False

    return True
