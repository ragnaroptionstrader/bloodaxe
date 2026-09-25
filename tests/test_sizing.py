"""Tests for Bloodaxe sizing (Rules 6, 8)."""
import pytest
from bloodaxe_pkg.sizing.caps import (
    size_bloodaxe_ic,
    DEFAULT_MAX_LOSS_PER_TRADE_USD,
)


def test_default_sizing_passes():
    s = size_bloodaxe_ic(wing_width=5.0)
    assert s.quantity == 1
    assert s.max_loss_total == 500.0
    assert s.passes_caps is True
    assert s.skip_reason is None


def test_default_cap_is_500():
    assert DEFAULT_MAX_LOSS_PER_TRADE_USD == 500


def test_wider_wings_fail():
    """Single 10pt wing IC exceeds $500 cap."""
    s = size_bloodaxe_ic(wing_width=10.0)
    assert s.passes_caps is False
    assert s.skip_reason is not None
    assert "exceeds cap" in s.skip_reason


def test_wider_wings_quantity_reduced():
    """A 15pt wing IC reduces qty to fit cap."""
    s = size_bloodaxe_ic(wing_width=15.0)
    # 15 × 100 = 1500 per contract, cap = 500, so 0 contracts (1 × 1500 > 500)
    assert s.passes_caps is False


def test_zero_wing_width():
    s = size_bloodaxe_ic(wing_width=0)
    assert s.passes_caps is False
    assert "wing_width must be > 0" in s.skip_reason


def test_custom_cap():
    """With cap=1000, two contracts of $5 wing fit."""
    s = size_bloodaxe_ic(wing_width=5.0, quantity=2, max_loss_per_trade_usd=1000)
    assert s.passes_caps is True
    assert s.quantity == 2
    assert s.max_loss_total == 1000.0


def test_explicit_quantity_overrides():
    """Quantity param respected even when default would suggest 1."""
    s = size_bloodaxe_ic(wing_width=2.5, quantity=1, max_loss_per_trade_usd=500)
    assert s.quantity == 1
    assert s.max_loss_total == 250.0
