"""Tests for Bloodaxe _check_chain_depth pre-check.

The chain-depth pre-check probes ±2 strikes around each target short strike.
If either side returns ZERO quoted strikes (mid > 0), the chain is
fundamentally sparse and we skip immediately with a clear operator-readable
reason. This saves the 12+ quote calls the strike-pair fallback would
otherwise waste on a hopeless chain.

Discovered 2026-10-06 with AMD on Tiger paper (chain has data only at
even strikes 220-270, all odd strikes return mid=0 or None).
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


def _quote_broker(quotes: dict):
    """Build a mock broker returning fixed mids for specific (strike, is_call) tuples.

    quotes keys are (strike, is_call) tuples. Missing → returns None.
    """
    broker = MagicMock()

    def _q(symbol, expiry, strike, is_call):
        key = (strike, is_call)
        mid = quotes.get(key)
        if mid is None:
            return None
        return {"mid": mid, "iv": 0.20}

    broker.get_option_quote.side_effect = _q
    return broker


# ---------------------------------------------------------------------------
# Happy path: both sides have quoted strikes in range
# ---------------------------------------------------------------------------

def test_chain_has_data_returns_ok(fresh_scan_module):
    """Calls AND puts both have ≥1 quoted strike in ±2 range → ok=True."""
    quotes = {
        # Calls near 750
        (745, True): 1.0, (750, True): 14.675, (755, True): 0.5,
        # Puts near 515
        (510, False): 7.225, (515, False): 0.5, (520, False): 8.55,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "AMD", "20261120",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert ok is True
    assert reason is None


def test_amd_real_chain_passes_check(fresh_scan_module):
    """Real AMD chain (paper, 2026-10-06): even strikes quoted, odd mid=0.
    Chain-depth check correctly identifies chain has data."""
    # Real AMD chain: 740, 745, 750, 755, 760 → even ones quoted, odd mid=0
    quotes = {
        (740, True): 15.825, (745, True): 0.0, (750, True): 14.675,
        (755, True): 0.0, (760, True): 12.8,
        (510, False): 7.225, (515, False): 0.0, (520, False): 8.55,
        (525, False): 0.0, (530, False): 10.075,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "AMD", "20261120",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    # Both sides have quoted strikes (evens) → chain has data → pass through
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# Sparse chain: calls or puts have ZERO quoted strikes
# ---------------------------------------------------------------------------

def test_calls_sparse_returns_skip_reason(fresh_scan_module):
    """Calls side has zero quoted strikes in range → skip with chain_sparse_calls."""
    quotes = {
        # No calls quoted anywhere
        # Puts are fine
        (515, False): 1.0, (520, False): 1.0, (525, False): 1.0,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert ok is False
    assert reason is not None
    assert "chain_sparse_calls" in reason
    assert "750" in reason


def test_puts_sparse_returns_skip_reason(fresh_scan_module):
    """Puts side has zero quoted strikes in range → skip with chain_sparse_puts."""
    quotes = {
        # Calls fine, no puts
        (745, True): 1.0, (750, True): 1.0, (755, True): 1.0,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert ok is False
    assert reason is not None
    assert "chain_sparse_puts" in reason
    assert "515" in reason


def test_all_quotes_zero_count_as_sparse(fresh_scan_module):
    """Mid=0 returns are NOT counted as quoted (excluded by > 0 check)."""
    quotes = {
        # All mids are 0 — these should be EXCLUDED
        (745, True): 0.0, (750, True): 0.0, (755, True): 0.0,
        (510, False): 0.0, (515, False): 0.0, (520, False): 0.0,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert ok is False
    assert "chain_sparse" in reason


def test_missing_quotes_treated_as_sparse(fresh_scan_module):
    """Missing quotes (None returned) are NOT counted as quoted."""
    quotes = {}  # Empty — all lookups return None
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert ok is False
    assert "chain_sparse_calls" in reason  # Calls checked first


# ---------------------------------------------------------------------------
# Search range configurability
# ---------------------------------------------------------------------------

def test_search_range_1_probes_only_3_strikes_per_side(fresh_scan_module):
    """search_range=1 → probes 3 strikes per side (target ± 1)."""
    quotes = {
        # Strike exactly at target + 1 is quoted (calls side)
        (751, True): 1.0,
        # Put side also needs quotes to pass
        (516, False): 1.0,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=1.0,  # use 1.0 step so 751 means ±1
        search_range=1,
    )
    # 749/750/751 checked; 751 has quote → ok
    assert ok is True


def test_search_range_2_default(fresh_scan_module):
    """Default search_range=2 (5 strikes per side)."""
    # Probes target ± 2 strikes = ± 10 for step=5
    # Calls near 750: probes 740-760, (745, True) = 1.0
    # Puts near 515: probes 505-525, (520, False) = 1.0
    quotes = {
        (745, True): 1.0,
        (520, False): 1.0,
    }
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0,
        # search_range defaults to 2
    )
    assert ok is True


# ---------------------------------------------------------------------------
# Quote count awareness
# ---------------------------------------------------------------------------

def test_calls_count_quotes_correctly(fresh_scan_module):
    """Verify the probe makes exactly 10 quote calls (5 calls + 5 puts)."""
    quotes = {
        (740, True): 1.0, (745, True): 1.0, (750, True): 1.0,
        (755, True): 1.0, (760, True): 1.0,
        (505, False): 1.0, (510, False): 1.0, (515, False): 1.0,
        (520, False): 1.0, (525, False): 1.0,
    }
    broker = _quote_broker(quotes)
    fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    # 5 call probes + 5 put probes = 10 total
    assert broker.get_option_quote.call_count == 10


def test_calls_checked_before_puts(fresh_scan_module):
    """If calls sparse, we don't even probe puts (saves 5 quotes)."""
    quotes = {}  # No quotes at all
    broker = _quote_broker(quotes)
    fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    # Only 5 call probes (calls checked first), then skip
    assert broker.get_option_quote.call_count == 5


# ---------------------------------------------------------------------------
# Skip reason format
# ---------------------------------------------------------------------------

def test_skip_reason_includes_strike_value(fresh_scan_module):
    """The skip reason includes the target strike value (operator can see what failed)."""
    quotes = {}  # No calls
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750, target_short_put=515,
        strike_step=5.0, search_range=2,
    )
    assert "750" in reason  # short_call target
    assert "near" in reason  # human-readable


def test_skip_reason_strike_rounded(fresh_scan_module):
    """Target strike shown without decimals in skip reason."""
    quotes = {}
    broker = _quote_broker(quotes)
    ok, reason = fresh_scan_module._check_chain_depth(
        broker, "TEST", "20261219",
        target_short_call=750.7,  # non-grid strike
        target_short_put=515,
        strike_step=5.0,
    )
    # 750.7 formatted as 751 (rounded)
    assert "751" in reason