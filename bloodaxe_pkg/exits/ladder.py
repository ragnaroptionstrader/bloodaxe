"""Bloodaxe exits — Rule 7.

Pre-defined exits are committed BEFORE the trade is entered:
- profit_take_pct: close when credit captured >= profit_take_pct (default 50%)
- stop_loss_multiple: close when loss >= stop_loss_multiple × credit (default 2x)
- dte_close: close when DTE reaches this threshold (default 7)
- wing_breach: defensive close if the underlying breaches a short strike

The exit is checked every cron tick (typically every 20 minutes during RTH).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExitReason(str, Enum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"
    WING_BREACH = "wing_breach"
    EXPIRY = "expiry"
    MANUAL = "manual"


@dataclass
class BloodaxePosition:
    """Live state for one Bloodaxe IC position."""
    order_id: str
    underlying: str
    expiry: str
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    wing_width: float
    entry_credit: float        # total credit received (per share × 100)
    quantity: int
    entry_time: str            # ISO timestamp
    entry_iv: float | None
    profit_take_pct: float = 0.50
    stop_loss_multiple: float = 2.0
    dte_close: int = 7

    def profit_target_price(self) -> float:
        """Price level to close at for profit target.

        For a short IC, profit = entry_credit - current_cost_to_close.
        Take profit when current_cost_to_close <= entry_credit × (1 - profit_take_pct).
        """
        return self.entry_credit * (1 - self.profit_take_pct)

    def stop_loss_price(self) -> float:
        """Price level to close at for stop loss.

        For a short IC, loss = current_cost_to_close - entry_credit.
        Stop out when current_cost_to_close >= entry_credit × (1 + stop_loss_multiple).
        """
        return self.entry_credit * (1 + self.stop_loss_multiple)


def evaluate_exit(
    pos: BloodaxePosition,
    *,
    current_cost_to_close: float,
    dte_remaining: int,
    current_spot: float,
) -> Optional[ExitReason]:
    """Decide if a Bloodaxe position should be closed now.

    Args:
        pos: the position state.
        current_cost_to_close: debit to close the IC right now (per share × 100).
        dte_remaining: days to expiry.
        current_spot: current underlying price.

    Returns:
        ExitReason if the position should close, None to hold.
    """
    # 1. Wing breach (defensive): underlying breached a short strike
    if current_spot <= pos.short_put or current_spot >= pos.short_call:
        return ExitReason.WING_BREACH

    # 2. Profit target: cost to close <= profit_target_price
    if current_cost_to_close <= pos.profit_target_price():
        return ExitReason.PROFIT_TARGET

    # 3. Stop loss: cost to close >= stop_loss_price
    if current_cost_to_close >= pos.stop_loss_price():
        return ExitReason.STOP_LOSS

    # 4. Time stop: DTE <= threshold
    if dte_remaining <= pos.dte_close:
        return ExitReason.TIME_STOP

    return None  # hold
