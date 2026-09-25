"""Tests for Bloodaxe liquidity filter (Rule 1)."""
from bloodaxe_pkg.scanner.liquidity import passes_liquidity


def make_quote(strike, mid=1.0, bid=1.0, ask=1.0, oi=200, vol=0, right="C"):
    spread = (ask - bid) / mid if mid > 0 else 1.0
    return {
        "strike": strike, "right": right, "mid": mid,
        "bid": bid, "ask": ask, "spread_pct": spread,
        "open_interest": oi, "volume": vol,
    }


def test_passes_liquid_chain():
    """ATM-ish strikes with tight spreads and high OI pass."""
    quotes = [
        make_quote(95, mid=1.0, bid=1.0, ask=1.0),
        make_quote(96, mid=1.1, bid=1.05, ask=1.15),
        make_quote(97, mid=1.2, bid=1.15, ask=1.25),
        make_quote(98, mid=1.3, bid=1.25, ask=1.35),
        make_quote(99, mid=1.4, bid=1.35, ask=1.45),
        make_quote(100, mid=1.5, bid=1.45, ask=1.55),  # ATM
        make_quote(101, mid=1.6, bid=1.55, ask=1.65),
        make_quote(102, mid=1.7, bid=1.65, ask=1.75),
        make_quote(103, mid=1.8, bid=1.75, ask=1.85),
        make_quote(104, mid=1.9, bid=1.85, ask=1.95),
        make_quote(105, mid=2.0, bid=1.95, ask=2.05),
    ]
    assert passes_liquidity(quotes) is True


def test_fails_too_few_strikes():
    quotes = [make_quote(100)] * 5  # only 5 strikes, all at same strike
    assert passes_liquidity(quotes) is False


def test_fails_wide_spread():
    quotes = [
        make_quote(95, mid=1.0, bid=0.5, ask=1.5),  # 100% spread
        make_quote(100, mid=1.0, bid=0.5, ask=1.5),
        make_quote(105, mid=1.0, bid=0.5, ask=1.5),
    ] * 4
    assert passes_liquidity(quotes) is False


def test_fails_no_oi_no_volume():
    quotes = [
        make_quote(100, mid=1.0, oi=10, vol=0),
    ] * 11
    assert passes_liquidity(quotes) is False


def test_zero_bid_quotes_skipped():
    """Zero-bid quotes don't fail the check (they're skipped)."""
    # Use 11 unique strikes; half have zero bid, half are liquid
    quotes = []
    for i in range(11):
        strike = 95 + i
        if i % 2 == 0:
            quotes.append(make_quote(strike, mid=0.0, bid=0.0, ask=0.0))
        else:
            quotes.append(make_quote(strike, mid=1.0, bid=1.0, ask=1.0))
    # Should pass on the 5+ liquid strikes
    assert passes_liquidity(quotes, min_strikes_per_side=5) is True
