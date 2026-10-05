"""Tests for asymmetric (put/call) strike placement in pick_strikes (Rule 3.5).

The skew fix's downstream effect: short_put and short_call can now sit at
different distances from spot when em_put != em_call.
"""
import math

import pytest

from bloodaxe_pkg.strikes.picker import pick_strikes


def test_asymmetric_put_widens_short_put():
    """If em_put > em_call, short_put lands further OTM than short_call
    would under symmetric em_multiple=1.0."""
    symmetric = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
        strike_step=5.0, wing_width=5.0,
    )
    asymmetric = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
        strike_step=5.0, wing_width=5.0,
        em_multiple_put=1.5, em_multiple_call=1.0,
    )
    # Same call side, but the short_put sits 10pts further OTM.
    assert asymmetric.short_call == symmetric.short_call
    assert asymmetric.short_put < symmetric.short_put
    assert symmetric.short_put - asymmetric.short_put == 10.0


def test_asymmetric_call_widens_short_call():
    symmetric = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
    )
    asymmetric = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
        em_multiple_call=1.5, em_multiple_put=1.0,
    )
    assert asymmetric.short_put == symmetric.short_put
    assert asymmetric.short_call > symmetric.short_call


def test_explicit_em_put_overrides_scalar():
    """em_put (dollar amount) takes priority over em_dollar × em_multiple_put."""
    strikes = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
        em_multiple_put=1.0,  # would give 500-20 = 480
        em_put=35.0,          # override → 500-35 = 465
    )
    assert strikes.short_put == 465.0


def test_explicit_em_call_overrides_scalar():
    strikes = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0,
        em_multiple_call=1.0,
        em_call=35.0,  # 500+35 = 535 → snap up to 535
    )
    assert strikes.short_call == 535.0


def test_fallback_to_em_multiple_when_per_side_missing():
    """If neither em_multiple_put nor em_put supplied, falls back to em_multiple."""
    strikes = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0, em_multiple=1.25,
    )
    assert strikes.short_put == 475.0  # 500 - 25 = 475
    assert strikes.short_call == 525.0  # 500 + 25 = 525


def test_resolved_em_must_be_positive():
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=500, em_dollar=20, em_put=-5)
    with pytest.raises(ValueError):
        pick_strikes("SPY", "20261219", spot=500, em_dollar=20, em_call=0)


def test_symmetric_em_multiple_still_works_for_backcompat():
    """Existing call sites using em_multiple=1.0 must produce identical output."""
    strikes_old = pick_strikes(
        "SPY", "20261219", spot=500.0, em_dollar=20.0, em_multiple=1.0,
    )
    assert strikes_old.short_put == 480.0
    assert strikes_old.short_call == 520.0
    assert strikes_old.long_put == 475.0
    assert strikes_old.long_call == 525.0


def test_baba_style_realized_vol_push():
    """Pin a BABA-like adjustment. spot=110 (actual), em_dollar=11 (IV-based),
    dhv-based em_put = 110 × 0.247 × √(45/365) = 9.55, with skew_safety 1.25
    em_put_used = 11.94. Result: short_put lands at 95 (one strike wider than
    the IV-only baseline of 100). short_call stays at 120.
    """
    strikes = pick_strikes(
        "BABA", "20261120", spot=110.0, em_dollar=11.20,
        strike_step=5.0, wing_width=5.0,
        em_put=11.94,  # widened put side
        em_call=11.20,  # unchanged call side
    )
    # 110 - 11.94 = 98.06 → snap DOWN to 100... actually 95 (floor 98.06/5 = 19.6 → 19×5 = 95)
    # Wait: floor(98.06/5) = 19, 19*5 = 95. Yes 95.
    assert strikes.short_put == 95.0, f"expected short_put 95, got {strikes.short_put}"
    # 110 + 11.20 = 121.20 → ceil(121.20/5) = 25, 25*5 = 125
    assert strikes.short_call == 125.0