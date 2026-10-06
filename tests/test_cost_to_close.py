"""Tests for Bloodaxe iron-condor cost-to-close calculator (Rule 7 fixed 2026-10-06).

The previous formula summed short-side asks only, ignoring the long-wing
proceeds. That overstated cost and triggered phantom stop-loss exits.
"""
import pytest

from bloodaxe_pkg.exits.cost_to_close import compute_ic_cost_to_close


def make_quote(bid, ask):
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}


def test_basic_cost_to_close():
    """SPY IC-style: $1.50 asks on shorts, $0.40 bids on longs → $220."""
    sc = make_quote(bid=1.45, ask=1.50)
    lc = make_quote(bid=0.40, ask=0.45)
    sp = make_quote(bid=1.45, ask=1.50)
    lp = make_quote(bid=0.40, ask=0.45)
    cost = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (1.50+1.50) - (0.40+0.40) = 3.00 - 0.80 = 2.20/share = $220
    assert abs(cost - 220.0) < 1e-6


def test_correct_formula_subtracts_long_proceeds():
    """The fix: long proceeds must reduce cost (not just short asks summed)."""
    sc = make_quote(bid=2.00, ask=2.50)  # ITM short, expensive to buy back
    lc = make_quote(bid=1.80, ask=1.90)  # Long has value
    sp = make_quote(bid=2.00, ask=2.50)
    lp = make_quote(bid=1.80, ask=1.90)
    cost = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (2.50+2.50) - (1.80+1.80) = 5.00 - 3.60 = 1.40/share = $140
    assert abs(cost - 140.0) < 1e-6
    # Old buggy formula would have said $500 (only short asks, no long offset)


def test_high_long_proceeds_negative_cost():
    """If longs are worth more than shorts cost (rare), result is negative."""
    sc = make_quote(bid=0.50, ask=0.60)
    lc = make_quote(bid=5.00, ask=5.10)
    sp = make_quote(bid=0.50, ask=0.60)
    lp = make_quote(bid=5.00, ask=5.10)
    cost = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (0.60+0.60) - (5.00+5.00) = 1.20 - 10.00 = -8.80/share = -$880
    # Negative = net credit on close (we'd actually receive money)
    assert abs(cost - (-880.0)) < 1e-6


def test_zero_quotes_zero_cost():
    """All zeros → zero cost (degenerate case)."""
    zero = make_quote(bid=0.0, ask=0.0)
    assert compute_ic_cost_to_close(zero, zero, zero, zero) == 0.0


def test_conservative_mode_for_wide_spreads():
    """use_bid_for_shorts=True → use bid instead of ask for shorts.
    More realistic when bid/ask spread is wide (illiquid name)."""
    sc = make_quote(bid=2.00, ask=2.50)  # 0.50 spread
    lc = make_quote(bid=1.80, ask=1.90)
    sp = make_quote(bid=2.00, ask=2.50)
    lp = make_quote(bid=1.80, ask=1.90)
    # Default (use_bid_for_shorts=False): (2.50+2.50) - (1.80+1.80) = $140
    normal = compute_ic_cost_to_close(sc, lc, sp, lp)
    assert abs(normal - 140.0) < 1e-6
    # Conservative: (2.00+2.00) - (1.80+1.80) = $40 (allow floating point tolerance)
    cost = compute_ic_cost_to_close(
        sc, lc, sp, lp, use_bid_for_shorts=True
    )
    assert abs(cost - 40.0) < 1e-6


def test_amd_wing_10_scenario():
    """Real-world AMD IC: large wings, smaller credit/proceeds."""
    # AMD SP=520 LC=760 SP=510 LP=510 (wing=10 each side)
    # After some time on market, longs lost some (ADBE approach)
    sc = make_quote(bid=13.50, ask=14.00)  # Short call
    lc = make_quote(bid=12.50, ask=13.00)  # Long call
    sp = make_quote(bid=8.00, ask=8.50)    # Short put
    lp = make_quote(bid=7.00, ask=7.50)    # Long put
    cost = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (14.00+8.50) - (12.50+7.00) = 22.50 - 19.50 = 3.00/share = $300
    assert abs(cost - 300.0) < 1e-6


def test_entry_credit_profit_calc():
    """Verify the formula gives expected profit/loss for a profitable position."""
    # At entry: short_call_ask=$1.50, long_call_bid=$0.40 (selling long)
    # At entry: short_put_ask=$1.50, long_put_bid=$0.40
    # Net entry credit = (1.50+1.50) - (0.40+0.40) = $2.20/share = $220/contract
    # After 50% profit: cost to close = $110
    sc = make_quote(bid=0.70, ask=0.75)
    lc = make_quote(bid=0.35, ask=0.40)
    sp = make_quote(bid=0.70, ask=0.75)
    lp = make_quote(bid=0.35, ask=0.40)
    cost = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (0.75+0.75) - (0.35+0.35) = 1.50 - 0.70 = 0.80/share = $80
    # Profit = 220 - 80 = $140 / 64% captured
    assert abs(cost - 80.0) < 1e-6


def test_missing_bid_key_raises():
    """Quote dict without required keys raises ValueError."""
    incomplete = {"ask": 1.0}  # no bid
    full = make_quote(bid=0.5, ask=1.0)
    with pytest.raises(ValueError, match="short_call_quote missing"):
        compute_ic_cost_to_close(incomplete, full, full, full)


def test_missing_ask_key_raises():
    incomplete = {"bid": 1.0}  # no ask
    full = make_quote(bid=0.5, ask=1.0)
    with pytest.raises(ValueError, match="long_put_quote missing"):
        compute_ic_cost_to_close(full, full, full, incomplete)


def test_old_buggy_formula_regression():
    """Regression test for the old bug: short asks summed without long offset.

    Before the fix, code was:
        cost_to_close_per_share = sc_q['ask'] + sp_q['ask']
    This omitted the long_call_bid + long_put_bid offset. We pin the
    CORRECT formula so any reversion to the old bug fails this test."""
    # Pick a position where the bug would have given a VERY different answer
    sc = make_quote(bid=2.00, ask=2.50)
    lc = make_quote(bid=1.50, ask=2.00)
    sp = make_quote(bid=2.00, ask=2.50)
    lp = make_quote(bid=1.50, ask=2.00)
    correct = compute_ic_cost_to_close(sc, lc, sp, lp)
    # (2.50+2.50) - (1.50+1.50) = 5.00 - 3.00 = 2.00/share = $200
    assert abs(correct - 200.0) < 1e-6
    # Old buggy: 2.50+2.50 = $500 — this test catches any reversion
    assert abs(correct - 500.0) > 1.0  # must NOT equal the buggy answer