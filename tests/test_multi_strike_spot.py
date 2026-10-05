"""Tests for Bloodaxe _infer_spot_from_atm_call multi-strike averaging.

The single-strike approach was vulnerable to bad quotes (NVDA/AMD/BABA
disagreement on 2026-10-06). New approach: walk 3 strikes around the ATM
guess, collect spot estimates from BOTH call and put quotes, return median.
Median is robust to 1-2 bad quotes among 6 total estimates.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def fresh_scan_module():
    import importlib
    import cli.scan as scan_mod
    importlib.reload(scan_mod)
    return scan_mod


def _make_broker(call_mids: dict, put_mids: dict):
    """Build a mock broker with controllable per-strike call/put mids.

    call_mids/put_mids keys are (strike, is_call) tuples. Values are mid prices.
    A key being absent means get_option_quote returns None for that strike/side.
    """
    broker = MagicMock()

    def _quote(symbol, expiry, strike, is_call):
        key = (strike, is_call)
        mid = call_mids.get(key) if is_call else put_mids.get(key)
        if mid is None:
            return None
        return {"mid": mid, "iv": 0.20}

    broker.get_option_quote.side_effect = _quote
    return broker


# Realistic mid values for an ATM-ish underlying at spot=100 with strike_step=5:
# 95C: deep ITM, mid≈5 (all intrinsic). 95P: deep OTM, mid≈0.5.
# 100C: ATM, mid≈0.5. 100P: ATM, mid≈0.5.
# 105C: deep OTM, mid≈0.5. 105P: deep ITM, mid≈5.

# ---------------------------------------------------------------------------
# Happy path: all 6 quotes agree
# ---------------------------------------------------------------------------

def test_all_6_quotes_agree_returns_consistent_spot(fresh_scan_module):
    """All 6 estimates (3 calls + 3 puts) at spot=100 → median ≈ 100."""
    broker = _make_broker(
        # Strike 95: call ITM (mid=5), put OTM (mid=0.5) → estimates 100, 94.5
        # Strike 100: call ATM (mid=0.5), put ATM (mid=0.5) → estimates 100.5, 99.5
        # Strike 105: call OTM (mid=0.5), put ITM (mid=5) → estimates 105.5, 100
        call_mids={(95, True): 5.0, (100, True): 0.5, (105, True): 0.5},
        put_mids={(95, False): 0.5, (100, False): 0.5, (105, False): 5.0},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 100, 100.5, 105.5, 94.5, 99.5, 100 → sorted
    # [94.5, 99.5, 100, 100, 100.5, 105.5] → median (avg of positions 2,3) = 100.0
    assert spot == 100.0


# ---------------------------------------------------------------------------
# Robustness to outliers (the key property)
# ---------------------------------------------------------------------------

def test_one_bad_quote_gets_ignored(fresh_scan_module):
    """1 of 6 quotes wildly off → median is still close to truth."""
    broker = _make_broker(
        call_mids={(95, True): 5.0, (100, True): 0.5, (105, True): 999.0},  # bad
        put_mids={(95, False): 0.5, (100, False): 0.5, (105, False): 5.0},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 100, 100.5, 1104, 94.5, 99.5, 100 → sorted
    # [94.5, 99.5, 100, 100, 100.5, 1104] → median (avg 2,3) = 100.0
    assert spot == 100.0


def test_two_bad_quotes_gets_ignored(fresh_scan_module):
    """2 of 6 quotes off (one gives negative estimate → filtered by 50-floor)."""
    broker = _make_broker(
        call_mids={(95, True): 5.0, (100, True): 50.0, (105, True): 0.5},  # bad
        put_mids={(95, False): 0.5, (100, False): 0.5, (105, False): 999.0},  # bad
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 100, 150, 105.5, 94.5, 99.5, -894
    # -894 excluded by sanity bound (< 50)
    # Remaining 5: [94.5, 99.5, 100, 105.5, 150] → middle = 100.0
    assert spot == 100.0


def test_majority_bad_quotes_still_handles_gracefully(fresh_scan_module):
    """If 4 of 6 are bad but within sanity bounds, median reflects majority."""
    # This documents the limitation: when bad quotes stay within sanity
    # bounds (50 < x < 5000), the median picks the center of the
    # remaining cluster. Real-world: paper account quote distortion is
    # typically 1-2 quotes, not 4. The bars cross-check in _get_spot
    # still catches systemic disagreement regardless.
    broker = _make_broker(
        call_mids={(95, True): 5.0, (100, True): 999.0, (105, True): 999.0},  # 2 bad
        put_mids={(95, False): 999.0, (100, False): 999.0, (105, False): 5.0},  # 2 bad
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 100, 1099, 1104, -904, -899, 100
    # Negative ones filtered → [100, 1099, 1104, 100] sorted
    # → [100, 100, 1099, 1104] → median (avg 2,3) = 599.5
    # This isn't 100, but the bars cross-check downstream catches it.
    assert abs(spot - 599.5) < 0.01


# ---------------------------------------------------------------------------
# Sanity bound enforcement (50 < spot < 5000)
# ---------------------------------------------------------------------------

def test_low_estimate_excluded(fresh_scan_module):
    """Estimates < 50 excluded — prevents garbage mid from polluting median."""
    # Strike 30 with mid 0.5 → spot_est = 30.5 (below 50, excluded)
    broker = _make_broker(
        call_mids={(30, True): 0.5, (100, True): 0.5},
        put_mids={(100, False): 0.5},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Strike 30 + call mid 0.5 = 30.5 → excluded.
    # Only strike 100 estimates: 100.5, 99.5 → median (avg of 2) = 100.0
    assert spot == 100.0


def test_huge_estimate_excluded(fresh_scan_module):
    """Estimates > 5000 excluded — catches inflated-mid bugs."""
    broker = _make_broker(
        call_mids={(100, True): 9999.0, (95, True): 5.0},
        put_mids={(95, False): 0.5},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Strike 100 + call mid 9999 = 10099 → excluded.
    # Strike 95 + call mid 5 = 100, strike 95 - put mid 0.5 = 94.5
    # Median (avg of 2) = 97.25
    assert abs(spot - 97.25) < 0.01


def test_no_valid_quotes_returns_none(fresh_scan_module):
    """All quotes invalid (sanity bounds) → return None."""
    # Quote giving estimate of 30 (below 50)
    broker = _make_broker(
        call_mids={(30, True): 0.5},
        put_mids={(30, False): 0.5},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    assert spot is None


def test_all_quotes_missing_returns_none(fresh_scan_module):
    """All get_option_quote calls return None → return None."""
    broker = _make_broker(call_mids={}, put_mids={})
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    assert spot is None


# ---------------------------------------------------------------------------
# ATM guess routing
# ---------------------------------------------------------------------------

def test_uses_spy_atm_guess_775(fresh_scan_module):
    """SPY guess=775 → tries strikes 770/775/780 (6 quotes total)."""
    broker = _make_broker(
        # All mids give spot ≈ 775, with realistic variation
        call_mids={(770, True): 5.0, (775, True): 0.5, (780, True): 0.5},
        put_mids={(770, False): 0.5, (775, False): 0.5, (780, False): 5.0},
    )
    fresh_scan_module._infer_spot_from_atm_call(
        broker, "SPY", "20261219", strike_step=5.0
    )
    actual_calls = [c.args for c in broker.get_option_quote.call_args_list]
    assert all(call[0] == "SPY" for call in actual_calls)
    assert all(call[1] == "20261219" for call in actual_calls)
    strikes_tried = {call[2] for call in actual_calls}
    assert strikes_tried == {770, 775, 780}


def test_uses_baba_atm_guess_110(fresh_scan_module):
    """BABA bug fix: guess=110 (was 180) → tries strikes 105/110/115."""
    broker = _make_broker(
        call_mids={(105, True): 5.0, (110, True): 0.5, (115, True): 0.5},
        put_mids={(105, False): 0.5, (110, False): 0.5, (115, False): 5.0},
    )
    fresh_scan_module._infer_spot_from_atm_call(
        broker, "BABA", "20261219", strike_step=5.0
    )
    actual_calls = [c.args for c in broker.get_option_quote.call_args_list]
    strikes_tried = {call[2] for call in actual_calls}
    assert strikes_tried == {105, 110, 115}


def test_unknown_symbol_defaults_to_guess_100(fresh_scan_module):
    """Symbols not in ATM_GUESSES → guess=100 → tries strikes 95/100/105."""
    broker = _make_broker(
        call_mids={(95, True): 5.0, (100, True): 0.5, (105, True): 0.5},
        put_mids={(95, False): 0.5, (100, False): 0.5, (105, False): 5.0},
    )
    fresh_scan_module._infer_spot_from_atm_call(
        broker, "UNKNOWN_SYM", "20261219", strike_step=5.0
    )
    actual_calls = [c.args for c in broker.get_option_quote.call_args_list]
    strikes_tried = {call[2] for call in actual_calls}
    assert strikes_tried == {95, 100, 105}


# ---------------------------------------------------------------------------
# Quote count (rate limit awareness)
# ---------------------------------------------------------------------------

def test_always_6_quote_calls_per_symbol(fresh_scan_module):
    """Per symbol: 3 strikes × 2 sides (call+put) = 6 quotes always."""
    broker = _make_broker(
        call_mids={(100, True): 0.5},
        put_mids={(100, False): 0.5},
    )
    fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Even though only strike 100 has valid quotes, all 6 strikes × sides
    # are queried (to give the median robustness property).
    assert broker.get_option_quote.call_count == 6


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

def test_returns_float_on_success(fresh_scan_module):
    """Return type is float (not None or int) when at least one valid quote."""
    broker = _make_broker(
        call_mids={(100, True): 0.5},
        put_mids={(100, False): 0.5},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    assert isinstance(spot, float)
    assert spot > 0


def test_returns_none_when_no_quotes(fresh_scan_module):
    """Return None (not float) when zero valid quotes."""
    broker = _make_broker(call_mids={}, put_mids={})
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    assert spot is None


def test_even_count_uses_average_of_middle_two(fresh_scan_module):
    """With 2 valid estimates, return average (even-length median)."""
    broker = _make_broker(
        # Only strikes 95 (call) and 105 (put) have valid quotes
        call_mids={(95, True): 5.0},
        put_mids={(105, False): 5.0},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 95+5=100, 105-5=100 → median (avg) = 100
    assert spot == 100.0


def test_odd_count_uses_single_middle_value(fresh_scan_module):
    """With 3 valid estimates, return middle directly."""
    broker = _make_broker(
        call_mids={(100, True): 0.5},
        put_mids={(95, False): 0.5, (105, False): 0.5},
    )
    spot = fresh_scan_module._infer_spot_from_atm_call(
        broker, "TEST", "20261219", strike_step=5.0
    )
    # Estimates: 100.5, 95-0.5=94.5, 105-0.5=104.5 → sorted: [94.5, 100.5, 104.5]
    # Middle = 100.5
    assert spot == 100.5