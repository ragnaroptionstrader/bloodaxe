"""Tests for Bloodaxe strike picker (Rules 4-5)."""
import pytest
from bloodaxe_pkg.strikes.picker import pick_strikes, BloodaxeStrikes


def test_pick_strikes_spy():
    """SPY spot $500, EM $20, 5pt wings → short strikes at 480 and 520."""
    strikes = pick_strikes(
        underlying="SPY",
        expiry="20261219",
        spot=500.0,
        em_dollar=20.0,
        strike_step=5.0,
        wing_width=5.0,
        em_multiple=1.0,
    )
    assert strikes.short_put == 480.0  # round down from 480
    assert strikes.long_put == 475.0
    assert strikes.short_call == 520.0
    assert strikes.long_call == 525.0
    assert strikes.wing_width == 5.0
    assert strikes.is_balanced


def test_pick_strikes_rounds_to_grid():
    """Strikes snap to nearest strike_step multiple."""
    strikes = pick_strikes(
        underlying="QQQ",
        expiry="20261219",
        spot=480.0,
        em_dollar=22.7,  # not on grid
        strike_step=5.0,
        wing_width=5.0,
    )
    # upper = 502.7 → snap up to 505
    assert strikes.short_call == 505.0
    # lower = 457.3 → snap down to 455
    assert strikes.short_put == 455.0


def test_pick_strikes_atm():
    """If spot is already on the grid, short strikes sit close to spot ± EM."""
    strikes = pick_strikes(
        underlying="SPY",
        expiry="20261219",
        spot=500.0,
        em_dollar=10.0,
    )
    assert strikes.short_call == 510.0
    assert strikes.short_put == 490.0


def test_pick_strikes_validation():
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=-1, em_dollar=10)
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=100, em_dollar=-10)
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=100, em_dollar=10, wing_width=-1)
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=100, em_dollar=10, strike_step=0)


def test_to_dict_round_trip():
    s = pick_strikes("SPY", "20261219", spot=500, em_dollar=20)
    d = s.to_dict()
    assert d["underlying"] == "SPY"
    assert d["wing_width"] == 5.0
    assert d["short_put"] == 480.0
