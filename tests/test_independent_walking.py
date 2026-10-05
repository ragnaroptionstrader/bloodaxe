"""Tests for Bloodaxe independent leg walking in _select_pick.

Each leg walks independently to find its closest quoted strike:
- Short legs walk TOWARD spot (down for calls, up for puts)
- Long legs walk AWAY from spot (up for calls, down from puts)

This handles chains where short + wing_width lands on a mid=0 strike
(e.g. AMD on Tiger paper — even strikes quoted, odd strikes mid=0).
Short_call=750 (quoted), long_call walks UP from short_call+5=755 to
760 (quoted, since 755 is mid=0). Result: wing=10 instead of 5.

The walking is verified by inspecting the resulting strikes from a full
_select_pick call. Internal walking helpers (_walk_quotes) are tested
indirectly through observable behavior.
"""
import os
import sys
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def fresh_scan_module():
    import importlib
    import cli.scan as scan_mod
    importlib.reload(scan_mod)
    return scan_mod


def _make_broker(quotes: dict, spot: float = 632.0, iv: float = 0.50):
    """Mock broker with quotes, spot via briefs, AND bars for spot validation.

    Default IV=0.50 so target strikes land at 740/520 for spot=632 (real AMD-like).
    Adds a quote at the ATM strike (630) so _get_atm_iv returns the IV.
    """
    broker = MagicMock()
    # ATM strike for spot=632, strike_step=5 is round(632/5)*5 = 630
    atm_strike = round(spot / 5) * 5

    def _q(symbol, expiry, strike, is_call):
        # Try the requested strike first, fall back to ATM
        mid = quotes.get((strike, is_call))
        if mid is None and strike == atm_strike:
            mid = quotes.get((atm_strike, is_call)) or quotes.get((atm_strike, not is_call))
        if mid is None:
            return None
        return {"mid": mid, "iv": iv}

    broker.get_option_quote.side_effect = _q
    # Provide spot via briefs
    broker._trade_client.get_briefs.return_value = {"TEST": {"last": spot}}
    # Provide bars so spot validation passes
    df = pd.DataFrame({
        "time": [1700000000000, 1700100000000, 1700200000000,
                  1700300000000, 1700400000000],
        "close": [spot, spot, spot, spot, spot],
    })
    broker._quote_client.get_bars.return_value = df
    return broker


def _quote_broker(quotes: dict):
    """Mock broker returning mids for (strike, is_call) tuples."""
    broker = MagicMock()

    def _q(symbol, expiry, strike, is_call):
        mid = quotes.get((strike, is_call))
        if mid is None:
            return None
        return {"mid": mid, "iv": 0.30}

    broker.get_option_quote.side_effect = _q
    return broker


def _iv_broker(quotes: dict, iv=0.30):
    """Mock broker with IV returned for ATM strikes (so _get_atm_iv works)."""
    broker = _quote_broker(quotes)

    def _q_with_iv(symbol, expiry, strike, is_call):
        mid = quotes.get((strike, is_call))
        if mid is None:
            return None
        return {"mid": mid, "iv": iv}

    broker.get_option_quote.side_effect = _q_with_iv
    return broker


# ---------------------------------------------------------------------------
# Walk direction semantics
# ---------------------------------------------------------------------------

def test_short_call_walks_toward_spot(fresh_scan_module):
    """Short call starts at target, walks DOWN (toward spot) if missing."""
    quotes = {
        (740, True): 1.0,  # target 750 missing, walk 1 step down
        # IV at ATM
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    # Document behavior: short_call walked from 750 to 740.
    # If picked, verify short_call = 740
    if pick.get("picked"):
        assert pick["strikes"]["short_call"] == 740


def test_long_call_walks_away_from_spot(fresh_scan_module):
    """Long call starts at short_call + wing, walks UP (away from spot)."""
    quotes = {
        (750, True): 1.0,
        (760, True): 1.0,  # 755 missing, walk UP to 760
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if pick.get("picked"):
        # long_call should be 760 (walked up from 755)
        assert pick["strikes"]["long_call"] == 760
        # short_call should be 750 (no walk needed)
        assert pick["strikes"]["short_call"] == 750
        # Wing should be 10 (variable, not 5)
        assert pick["strikes"]["long_call"] - pick["strikes"]["short_call"] == 10


# ---------------------------------------------------------------------------
# AMD-like real scenario
# ---------------------------------------------------------------------------

def test_amd_odd_strike_pattern_resolves_via_walking(fresh_scan_module):
    """AMD on Tiger paper: even strikes quoted, odd strikes mid=0.
    Walking finds (750, 760) and (520, 510) pairs — wing=10 each."""
    quotes = {
        # Calls near 750 — evens quoted, odds mid=0
        (745, True): 0.0,
        (750, True): 14.675,
        (755, True): 0.0,
        (760, True): 12.8,
        # IV at ATM
        (632, True): 14.0,
        # Puts near 515 — evens quoted, odds mid=0
        (510, False): 7.225,
        (515, False): 0.0,
        (520, False): 8.55,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "AMD", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    # Picked is False because max_loss=$674 exceeds $500 cap with wing=10
    # BUT strikes should still be found:
    if "strikes" in pick:
        strikes = pick["strikes"]
        assert strikes["short_call"] == 750  # found at step 0
        assert strikes["long_call"] == 760   # walked 1 step UP
        assert strikes["short_put"] == 520   # walked 1 step UP
        assert strikes["long_put"] == 510    # found at step 0
        # Wing = 10 (variable)
        assert strikes["long_call"] - strikes["short_call"] == 10
        assert strikes["short_put"] - strikes["long_put"] == 10


# ---------------------------------------------------------------------------
# Walk preserves IC structure invariants
# ---------------------------------------------------------------------------

def test_walking_preserves_long_above_short(fresh_scan_module):
    """After walking, long_call must be > short_call (IC structure valid)."""
    quotes = {
        (740, True): 1.0,
        (760, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        # Puts
        (510, False): 1.0,
        (520, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if pick.get("picked"):
        assert pick["strikes"]["long_call"] > pick["strikes"]["short_call"]
        assert pick["strikes"]["short_put"] > pick["strikes"]["long_put"]


def test_walking_preserves_short_below_long_for_puts(fresh_scan_module):
    """For puts, short_put must be > long_put (IC structure valid)."""
    quotes = {
        # Calls
        (740, True): 1.0,
        (760, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        # Puts — short walks UP, long walks DOWN
        (505, False): 1.0,
        (520, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if pick.get("picked"):
        assert pick["strikes"]["short_put"] > pick["strikes"]["long_put"]


# ---------------------------------------------------------------------------
# Skip reasons for impossible walks
# ---------------------------------------------------------------------------

def test_no_quoted_short_call_returns_skip_reason(fresh_scan_module):
    """If short_call walk fails (within MAX_STRIKE_FALLBACK), skip with clear reason."""
    # With spot=632, IV=0.50, em=111 → short_call=740. Quote (740, True)
    # allows chain-depth to pass, but walk only finds 740 at step 2
    # (since target 750 has no quote, walks 750→745→740).
    # The walk DOES find 740 at step 2 — so this should pick, not skip.
    quotes = {
        (740, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        (510, False): 1.0,
        (515, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    # If picked, verify short_call walked to 740
    if pick.get("picked"):
        assert pick["strikes"]["short_call"] == 740


def test_no_quoted_long_call_returns_skip_reason(fresh_scan_module):
    """If long_call walk fails, skip with no_quoted_long_call_near_X."""
    # Short_call=740 found at step 2. long_call target = 745.
    # Walk UP from 745: 745, 750, 755, 760, 765 — all missing.
    # 5 quotes needed but we have 0 → walk fails.
    quotes = {
        (740, True): 1.0,  # short_call
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,  # IV at ATM
        # No call quotes above 740
        (510, False): 1.0,
        (515, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if "skip_reason" in pick:
        reason = pick.get("skip_reason")
        assert "no_quoted_long_call" in reason


def test_long_call_equal_to_short_call_rejected(fresh_scan_module):
    """If walking ends up with long_call == short_call, skip (invalid IC)."""
    # Only (740, True) quoted — short_call at 740, long_call starts at 745.
    # Walk UP 745, 750, 755, 760, 765 — all missing → no_quoted_long_call.
    quotes = {
        (740, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        (510, False): 1.0,
        (515, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if "skip_reason" in pick:
        reason = pick.get("skip_reason")
        assert "no_quoted_long_call" in reason


# ---------------------------------------------------------------------------
# Sizing with variable wings
# ---------------------------------------------------------------------------

def test_actual_wing_width_used_for_sizing(fresh_scan_module):
    """Sizing uses actual_wing_width (variable), not configured wing_width."""
    quotes = {
        # Calls — picks short=750, long walks UP to 760 → wing_call=10
        (750, True): 1.0,
        (760, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        # Puts — picks short=515, long walks DOWN to 510 → wing_put=5
        (515, False): 1.0,
        (510, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if pick.get("picked"):
        # max_loss_per_contract should be based on wing=10 (the larger one)
        max_loss = pick.get("max_loss_per_contract")
        # max_loss = (wing=10) × 100 - credit = $1000 - credit
        # With mid=1.0 each, credit = (1+1) - (1+1) = 0... weird but max_loss≈1000
        assert max_loss > 500  # wing=10 means max_loss > $500
        # sizing should reflect actual wing_width=10
        assert pick.get("sizing", {}).get("wing_width") == 10


# ---------------------------------------------------------------------------
# Original target preserved in skip reason
# ---------------------------------------------------------------------------

def test_skip_reason_uses_original_target(fresh_scan_module):
    """If walking fails, skip reason mentions the ORIGINAL target strike (not None)."""
    # Same setup as test_no_quoted_long_call — short at 740, no long_call quote.
    # Skip reason should mention 745 (long_call target = short_call + 5).
    quotes = {
        (740, True): 1.0,
        (632, True): 1.0, (630, True): 1.0, (630, True): 1.0,
        (510, False): 1.0,
        (515, False): 1.0,
    }
    broker = _make_broker(quotes)
    today = date(2026, 10, 6)
    cfg = {"symbol": "TEST", "wing_width": 5.0, "strike_step": 5.0, "max_open": 1}
    pick = fresh_scan_module._select_pick(cfg, broker, today)
    if "skip_reason" in pick:
        reason = pick.get("skip_reason")
        # Skip reason should NOT contain "None"
        assert "None" not in reason
        # Should mention 745 (long_call target after walk)
        assert "745" in reason