"""Tests for Bloodaxe expected move (Rule 3)."""
import math
import pytest
from bloodaxe_pkg.expected_move.calc import (
    expected_move_from_iv,
    expected_move_from_straddle,
    em_band,
)


def test_em_from_iv_basic():
    # SPY $500, 25% IV, 30 DTE
    em = expected_move_from_iv(spot=500.0, iv=0.25, dte=30)
    expected = 500.0 * 0.25 * math.sqrt(30 / 365)
    assert math.isclose(em, expected, rel_tol=1e-9)


def test_em_from_iv_zero_dte():
    with pytest.raises(ValueError):
        expected_move_from_iv(spot=500.0, iv=0.25, dte=0)


def test_em_from_iv_negative():
    with pytest.raises(ValueError):
        expected_move_from_iv(spot=-100, iv=0.25, dte=30)


def test_em_from_straddle_basic():
    em = expected_move_from_straddle(straddle_price=12.5)
    assert em == 12.5


def test_em_band():
    lower, upper = em_band(spot=500.0, em_dollar=20.0)
    assert lower == 480.0
    assert upper == 520.0


def test_em_scales_with_dte():
    em_short = expected_move_from_iv(spot=500.0, iv=0.25, dte=7)
    em_long = expected_move_from_iv(spot=500.0, iv=0.25, dte=30)
    # Long DTE should give larger EM
    assert em_long > em_short
    ratio = em_long / em_short
    assert math.isclose(ratio, math.sqrt(30 / 7), rel_tol=1e-9)
