"""Tests for Bloodaxe exits (Rule 7)."""
import pytest
from bloodaxe_pkg.exits.ladder import (
    BloodaxePosition,
    evaluate_exit,
    ExitReason,
)


def make_pos(entry_credit=1.0, **overrides):
    defaults = {
        "order_id": "test-1",
        "underlying": "SPY",
        "expiry": "20261219",
        "short_put": 480.0,
        "long_put": 475.0,
        "short_call": 520.0,
        "long_call": 525.0,
        "wing_width": 5.0,
        "entry_credit": entry_credit,
        "quantity": 1,
        "entry_time": "2026-09-25T00:00:00+00:00",
        "entry_iv": 0.25,
        "profit_take_pct": 0.50,
        "stop_loss_multiple": 2.0,
        "dte_close": 7,
    }
    defaults.update(overrides)
    return BloodaxePosition(**defaults)


def test_profit_target_hit():
    """Cost to close <= 50% of credit → profit target."""
    pos = make_pos(entry_credit=1.0)
    # Cost = 0.40 (60% of credit captured as profit)
    assert evaluate_exit(pos, current_cost_to_close=0.40, dte_remaining=20, current_spot=500.0) == ExitReason.PROFIT_TARGET


def test_profit_target_not_hit():
    pos = make_pos(entry_credit=1.0)
    # Cost = 0.70 (only 30% captured, not enough)
    assert evaluate_exit(pos, current_cost_to_close=0.70, dte_remaining=20, current_spot=500.0) is None


def test_stop_loss_hit():
    pos = make_pos(entry_credit=1.0)
    # Cost = 3.0 (2x credit, stop loss)
    assert evaluate_exit(pos, current_cost_to_close=3.0, dte_remaining=20, current_spot=500.0) == ExitReason.STOP_LOSS


def test_time_stop_hit():
    pos = make_pos(entry_credit=1.0, dte_close=7)
    # Cost = 0.60 (no profit target — needs ≤0.50; no stop — needs ≥3.00)
    # DTE = 7 (at threshold)
    assert evaluate_exit(pos, current_cost_to_close=0.60, dte_remaining=7, current_spot=500.0) == ExitReason.TIME_STOP


def test_wing_breach_put():
    pos = make_pos()
    # Spot below short_put
    assert evaluate_exit(pos, current_cost_to_close=0.50, dte_remaining=20, current_spot=475.0) == ExitReason.WING_BREACH


def test_wing_breach_call():
    pos = make_pos()
    # Spot above short_call
    assert evaluate_exit(pos, current_cost_to_close=0.50, dte_remaining=20, current_spot=525.0) == ExitReason.WING_BREACH


def test_hold_when_no_trigger():
    pos = make_pos(entry_credit=1.0, dte_close=7)
    # Cost = 0.80, DTE = 20 (not at time stop), spot = 500 (not breached)
    assert evaluate_exit(pos, current_cost_to_close=0.80, dte_remaining=20, current_spot=500.0) is None


def test_profit_target_priority_over_stop():
    """If both would trigger, profit target wins (lower cost first)."""
    pos = make_pos(entry_credit=1.0)
    # Cost = 0.30 (profit) AND cost = 0.30 (not stop)
    # Profit target wins because it's evaluated first
    result = evaluate_exit(pos, current_cost_to_close=0.30, dte_remaining=20, current_spot=500.0)
    assert result == ExitReason.PROFIT_TARGET
