"""Bloodaxe expected move — Rule 3.

Computes the expected move (EM) for a chosen DTE using either:
- ATM straddle price: em_dollar = straddle_price (already the EM)
- IV: em_pct = iv * sqrt(DTE/365); em_dollar = spot * em_pct

EM tells us where the market expects the underlying to land by expiry
with ~68% probability (1 std dev). Strike selection outside this range
gives us a structural edge.
"""
from __future__ import annotations

import math


def expected_move_from_iv(spot: float, iv: float, dte: int) -> float:
    """EM in dollars using IV: em = spot * iv * sqrt(DTE/365).

    Args:
        spot: current underlying price.
        iv: implied volatility as a decimal (e.g., 0.25 for 25%).
        dte: days to expiry.

    Returns:
        Expected move in dollars (1 std dev).
    """
    if spot <= 0 or iv <= 0 or dte <= 0:
        raise ValueError(f"spot/iv/dte must be positive: spot={spot} iv={iv} dte={dte}")
    return spot * iv * math.sqrt(dte / 365.0)


def expected_move_from_straddle(straddle_price: float) -> float:
    """EM in dollars using ATM straddle price (already the 1-std-dev EM).

    Args:
        straddle_price: ATM call mid + ATM put mid.

    Returns:
        Expected move in dollars (1 std dev).
    """
    if straddle_price < 0:
        raise ValueError(f"straddle_price must be non-negative: {straddle_price}")
    return straddle_price


def em_band(spot: float, em_dollar: float) -> tuple[float, float]:
    """Return the (lower, upper) 1-std-dev band around spot.

    Args:
        spot: current underlying price.
        em_dollar: expected move in dollars.

    Returns:
        (lower, upper) tuple representing the 1-std-dev band.
    """
    return (spot - em_dollar, spot + em_dollar)
