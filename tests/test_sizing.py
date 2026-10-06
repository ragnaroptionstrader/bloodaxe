"""Tests for Bloodaxe sizing (Rules 6, 8)."""
import pytest
from bloodaxe_pkg.sizing.caps import (
    size_bloodaxe_ic,
    DEFAULT_MAX_LOSS_PER_TRADE_USD,
)


def test_default_sizing_passes():
    """Wing=5 with credit=0 (worst case): max_loss=$500, fits default cap."""
    s = size_bloodaxe_ic(wing_width=5.0)
    assert s.quantity == 1
    assert s.max_loss_total == 500.0
    assert s.passes_caps is True
    assert s.skip_reason is None


def test_default_cap_is_configurable():
    """Default cap is configurable via BLOODAXE_MAX_LOSS_PER_TRADE_USD env var.
    Defaults to $750 (bumped from $500 on 2026-10-06 to allow AMD wing=10).
    We test it's a positive int — exact value depends on env var."""
    assert isinstance(DEFAULT_MAX_LOSS_PER_TRADE_USD, int)
    assert DEFAULT_MAX_LOSS_PER_TRADE_USD > 0
    # Default is $750 (operator's directive 2026-10-06)
    assert DEFAULT_MAX_LOSS_PER_TRADE_USD == 750


def test_wider_wings_fail():
    """Single 10pt wing IC with no credit exceeds default cap."""
    s = size_bloodaxe_ic(wing_width=10.0)  # no credit → max_loss=$1000
    assert s.passes_caps is False
    assert s.skip_reason is not None
    assert "exceeds cap" in s.skip_reason


def test_wider_wings_with_credit_pass():
    """Wing=10 with $3.26/share credit → max_loss=$674, fits $750 cap.

    This is the AMD paper scenario (independent_walking finds wing=10
    because Tiger paper has odd-strike-mid=0 quirk). With credit
    subtracted, actual max_loss fits within the bumped cap."""
    s = size_bloodaxe_ic(wing_width=10.0, net_credit_per_share=3.26)
    assert s.passes_caps is True
    assert s.quantity == 1
    # max_loss_per_contract = 10*100 - 3.26*100 = 674
    assert abs(s.max_loss_per_contract - 674.0) < 1.0


def test_wider_wings_quantity_reduced():
    """A 15pt wing IC with no credit reduces qty to fit cap."""
    s = size_bloodaxe_ic(wing_width=15.0)
    # 15 × 100 = 1500 per contract, cap = 750, so 0 contracts
    assert s.passes_caps is False
    assert s.quantity == 0


def test_zero_wing_width():
    s = size_bloodaxe_ic(wing_width=0)
    assert s.passes_caps is False
    assert "wing_width must be > 0" in s.skip_reason


def test_custom_cap():
    """With cap=1000 and no credit, two contracts of $5 wing fit."""
    s = size_bloodaxe_ic(wing_width=5.0, quantity=2, max_loss_per_trade_usd=1000)
    assert s.passes_caps is True
    assert s.quantity == 2
    assert s.max_loss_total == 1000.0


def test_explicit_quantity_overrides():
    """Quantity param respected even when default would suggest 1."""
    s = size_bloodaxe_ic(wing_width=2.5, quantity=1, max_loss_per_trade_usd=500)
    assert s.quantity == 1
    assert s.max_loss_total == 250.0


def test_negative_credit_clamped_to_zero():
    """If net_credit is somehow negative, max_loss is clamped to wing*100."""
    # Defensive — shouldn't happen in practice but guards against bugs.
    s = size_bloodaxe_ic(wing_width=5.0, net_credit_per_share=-1.0)
    # Should be clamped: max(0, 5*100 - (-1)*100) = max(0, 600) = 600
    assert s.max_loss_per_contract == 600.0


def test_env_var_overrides_default():
    """BLOODAXE_MAX_LOSS_PER_TRADE_USD env var overrides module default."""
    import os
    import importlib
    import bloodaxe_pkg.sizing.caps as caps

    # Save original
    original = os.environ.get("BLOODAXE_MAX_LOSS_PER_TRADE_USD")
    try:
        os.environ["BLOODAXE_MAX_LOSS_PER_TRADE_USD"] = "1234"
        importlib.reload(caps)
        assert caps.DEFAULT_MAX_LOSS_PER_TRADE_USD == 1234
    finally:
        if original is None:
            os.environ.pop("BLOODAXE_MAX_LOSS_PER_TRADE_USD", None)
        else:
            os.environ["BLOODAXE_MAX_LOSS_PER_TRADE_USD"] = original
        # Reload to original
        importlib.reload(caps)
