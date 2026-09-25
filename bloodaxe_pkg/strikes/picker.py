"""Bloodaxe strike picker — Rules 4-5.

Identifies a reasonable upper/lower range based on expected move,
then picks 4 strikes for an iron condor:
- short_put at the lower bound (1 std dev below spot)
- long_put = short_put - wing_width
- short_call at the upper bound (1 std dev above spot)
- long_call = short_call + wing_width

The short strikes sit AT the 1-std-dev band — outside the expected
move for ~84% probability of profit on each side (16-delta equivalent).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BloodaxeStrikes:
    """4 strikes for an iron condor."""
    underlying: str
    expiry: str        # YYYYMMDD
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    wing_width: float
    expected_move: float
    spot: float

    @property
    def put_wing_size(self) -> float:
        return self.short_put - self.long_put

    @property
    def call_wing_size(self) -> float:
        return self.long_call - self.short_call

    @property
    def is_balanced(self) -> bool:
        """True if both wings have the same width (required for symmetric IC)."""
        return abs(self.put_wing_size - self.call_wing_size) < 0.01

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "expiry": self.expiry,
            "short_put": self.short_put,
            "long_put": self.long_put,
            "short_call": self.short_call,
            "long_call": self.long_call,
            "wing_width": self.wing_width,
            "expected_move": self.expected_move,
            "spot": self.spot,
        }


def pick_strikes(
    underlying: str,
    expiry: str,
    spot: float,
    em_dollar: float,
    *,
    strike_step: float = 5.0,
    wing_width: float = 5.0,
    em_multiple: float = 1.0,
) -> BloodaxeStrikes:
    """Pick 4 strikes for an iron condor around the expected-move band.

    Args:
        underlying: ticker symbol.
        expiry: YYYYMMDD format.
        spot: current underlying price.
        em_dollar: expected move in dollars (1 std dev).
        strike_step: strike price increment (default $5).
        wing_width: width of each wing (default $5).
        em_multiple: multiplier on EM for short strikes (default 1.0 = at EM).

    Returns:
        BloodaxeStrikes with 4 strikes snapped to the strike grid.

    Logic:
        short_call = round_up(spot + em_multiple × EM, strike_step)
        long_call = short_call + wing_width
        short_put = round_down(spot - em_multiple × EM, strike_step)
        long_put = short_put - wing_width
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive: {spot}")
    if em_dollar <= 0:
        raise ValueError(f"em_dollar must be positive: {em_dollar}")
    if wing_width <= 0:
        raise ValueError(f"wing_width must be positive: {wing_width}")
    if strike_step <= 0:
        raise ValueError(f"strike_step must be positive: {strike_step}")

    upper_target = spot + em_multiple * em_dollar
    lower_target = spot - em_multiple * em_dollar

    # Snap to strike grid (round up for upper, round down for lower)
    short_call = _round_up(upper_target, strike_step)
    long_call = short_call + wing_width
    short_put = _round_down(lower_target, strike_step)
    long_put = short_put - wing_width

    return BloodaxeStrikes(
        underlying=underlying,
        expiry=expiry,
        short_put=short_put,
        long_put=long_put,
        short_call=short_call,
        long_call=long_call,
        wing_width=wing_width,
        expected_move=em_dollar,
        spot=spot,
    )


def _round_up(value: float, step: float) -> float:
    """Round UP to the nearest multiple of step."""
    return math.ceil(value / step) * step


def _round_down(value: float, step: float) -> float:
    """Round DOWN to the nearest multiple of step."""
    return math.floor(value / step) * step


import math  # imported at bottom for clarity; used by _round_up/_round_down
