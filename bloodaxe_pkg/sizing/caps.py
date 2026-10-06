"""Bloodaxe sizing — Rules 6, 8.

Default sizing: 1 contract per trade, wing_width × 100 = max loss ceiling.

The cap is operator-configurable. Default is $750/condor (bumped from
$500 on 2026-10-06 per operator directive to enable paper validation
on AMD, where Tiger paper's odd-strike-mid=0 quirk forces wing=10 ICs
→ max_loss=$674 exceeds the old $500 cap. Live account: AMD fits with
wing=5 (cap relaxes naturally).

Risk envelope:
- max_loss_per_trade = $750 (paper) / $500 (live, recommended)
- max_concurrent = 5
- max_daily_loss = $750 (effectively one trade)

To override at runtime: pass max_loss_per_trade_usd to size_bloodaxe_ic.
"""
from __future__ import annotations

import os

from dataclasses import dataclass


# Default operator config (overridable via env or strategy config).
# Env var BLOODAXE_MAX_LOSS_PER_TRADE_USD wins over the constant — useful
# for runtime tuning without code changes.
DEFAULT_MAX_LOSS_PER_TRADE_USD = int(
    os.environ.get("BLOODAXE_MAX_LOSS_PER_TRADE_USD", "750")
)
DEFAULT_MAX_CONCURRENT_POSITIONS = 5
DEFAULT_MAX_DAILY_LOSS_USD = 750


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
    net_credit_per_share: float = 0.0,
    max_loss_per_trade_usd: float = DEFAULT_MAX_LOSS_PER_TRADE_USD,
) -> BloodaxeSize:
    """Size an iron condor so max loss stays within the cap.

    Args:
        wing_width: width of each wing in dollars (e.g., 5.0 for SPY $5 wings).
        quantity: number of contracts (default 1).
        net_credit_per_share: credit received per share at entry (e.g., 1.50 for
            $1.50/share). Used to compute ACTUAL max_loss = wing*100 - credit*100
            (not the conservative wing*100 estimate). Defaults to 0 (worst case).
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

    # ACTUAL max_loss accounts for credit already collected. Worst case (no
    # credit) is wing*100; typical case is wing*100 - credit*100. Using the
    # actual figure lets wing=10 ICs on AMD (max_loss $674 with credit)
    # pass the $750 cap, where conservative wing*100=$1000 would reject.
    max_loss_per_contract = max(0.0, wing_width * 100 - net_credit_per_share * 100)
    max_loss_total = quantity * max_loss_per_contract

    if max_loss_total > max_loss_per_trade_usd:
        # Reduce quantity to fit cap
        quantity = int(max_loss_per_trade_usd // max_loss_per_contract)
        if quantity < 1:
            return BloodaxeSize(
                quantity=0, wing_width=wing_width,
                max_loss_per_contract=max_loss_per_contract,
                max_loss_total=max_loss_per_contract,
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
