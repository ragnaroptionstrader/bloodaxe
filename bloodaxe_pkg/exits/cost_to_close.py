"""Cost-to-close calculator for Bloodaxe iron condors.

Fixed 2026-10-06: the previous formula only summed short-side asks,
ignoring the long-wing proceeds. That overstated the cost, triggering
phantom stop-loss exits within minutes of opening.

CORRECT formula for closing an IC:
- Shorts cost money to close: buy back at ASK
- Longs have value when closing: sell at BID
- Net cost = (short_close - long_proceeds) × 100 (per contract)

Example (SPY IC at entry_credit=$1.50):
  short_call ask=$1.50, long_call bid=$0.40, short_put ask=$1.50, long_put bid=$0.40
  Cost = (1.50+1.50) - (0.40+0.40) = 2.60 - 0.80 = $1.80/share = $180/contract
  P&L = entry_credit - cost = 1.50 - 1.80 = -$0.30/share = -$30/contract (small loss)
  Old formula would have said: cost = 1.50+1.50 = $3.00/share = $300/contract
  (incorrectly suggesting a $150 loss = 100% of credit captured)
"""
from __future__ import annotations

from typing import Mapping


def compute_ic_cost_to_close(
    short_call_quote: Mapping,
    long_call_quote: Mapping,
    short_put_quote: Mapping,
    long_put_quote: Mapping,
    *,
    use_bid_for_shorts: bool = False,
) -> float:
    """Compute the cost to close an iron condor (per contract).

    Args:
        short_call_quote: quote dict for the short call (must have 'ask' + 'bid').
        long_call_quote: quote dict for the long call (must have 'bid' + 'ask').
        short_put_quote: quote dict for the short put (must have 'ask' + 'bid').
        long_put_quote: quote dict for the long put (must have 'bid' + 'ask').
        use_bid_for_shorts: if True, use bid instead of ask for shorts (for
            a more conservative estimate — useful when bid/ask spread is wide).

    Returns:
        Cost to close in DOLLARS (per contract, after × 100 multiplier).
        Positive = debit (cost). Negative = credit (wouldn't happen on close,
        but kept for symmetry).

    Raises:
        ValueError: if any quote dict is missing required keys.
    """
    short_px_key = "bid" if use_bid_for_shorts else "ask"
    for name, q in [
        ("short_call", short_call_quote),
        ("long_call", long_call_quote),
        ("short_put", short_put_quote),
        ("long_put", long_put_quote),
    ]:
        if "bid" not in q or "ask" not in q:
            raise ValueError(f"{name}_quote missing bid/ask keys")

    short_close = short_call_quote[short_px_key] + short_put_quote[short_px_key]
    long_proceeds = long_call_quote["bid"] + long_put_quote["bid"]
    cost_to_close_per_share = short_close - long_proceeds
    return cost_to_close_per_share * 100  # per contract