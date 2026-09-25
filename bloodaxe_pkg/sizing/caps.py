"""Bloodaxe sizing — Rules 6, 8.

Default sizing: 1 contract per trade, wing_width × 100 = max loss ceiling.

The cap is operator-configurable but defaults to $500/condor (matching
VIDAR's $500 cap and the operator's 2026-09-25 directive).

Risk envelope:
- max_loss_per_trade = $500
- max_concurrent = 5
- max_daily_loss = $500 (effectively one trade)
"""
from __future__ import annotations

from dataclasses import dataclass


# Default operator config (overridable via env or strategy config)
DEFAULT_MAX_LOSS_PER_TRADE_USD = 500
DEFAULT_MAX_CONCURRENT_POSITIONS = 5
DEFAULT_MAX_DAILY_LOSS_USD = 500


@dataclass
class BloodaxeSize:
    """Sizing decision for one IC trade."""
    quantity: int            # number of contracts (default 1)
    wing_width: float        # width of each wing in $
    max_loss_per_contract: float
    max_loss_total: float    # quantity × wing_width × 100
    passes_caps: bool
    skip_reason: str | None

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "wing_width": self.wing_width,
            "max_loss_per_contract": self.max_loss_per_contract,
            "max_loss_total": self.max_loss_total,
            "passes_caps": self.passes_caps,
            "skip_reason": self.skip_reason,
        }


def size_bloodaxe_ic(
    wing_width: float,
    *,
    quantity: int = 1,
    max_loss_per_trade_usd: float = DEFAULT_MAX_LOSS_PER_TRADE_USD,
) -> BloodaxeSize:
    """Size an iron condor so max loss stays within the cap.

    Args:
        wing_width: width of each wing in dollars (e.g., 5.0 for SPY $5 wings).
        quantity: number of contracts (default 1).
        max_loss_per_trade_usd: max loss ceiling per trade.

    Returns:
        BloodaxeSize with quantity, max loss, and pass/fail verdict.
    """
    if wing_width <= 0:
        return BloodaxeSize(
            quantity=0, wing_width=wing_width,
            max_loss_per_contract=0, max_loss_total=0,
            passes_caps=False, skip_reason="wing_width must be > 0",
        )

    max_loss_per_contract = wing_width * 100
    max_loss_total = quantity * max_loss_per_contract

    if max_loss_total > max_loss_per_trade_usd:
        # Reduce quantity to fit cap
        quantity = int(max_loss_per_trade_usd // max_loss_per_contract)
        if quantity < 1:
            return BloodaxeSize(
                quantity=0, wing_width=wing_width,
                max_loss_per_contract=max_loss_per_contract,
                max_loss_total=wing_width * 100,
                passes_caps=False,
                skip_reason=f"single contract max loss ${max_loss_per_contract:.0f} exceeds cap ${max_loss_per_trade_usd:.0f}",
            )
        max_loss_total = quantity * max_loss_per_contract

    return BloodaxeSize(
        quantity=quantity,
        wing_width=wing_width,
        max_loss_per_contract=max_loss_per_contract,
        max_loss_total=max_loss_total,
        passes_caps=True,
        skip_reason=None,
    )
