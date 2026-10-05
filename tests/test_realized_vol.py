"""Tests for Bloodaxe realized-vol + downside-skew adjustment (Rule 3.5).

The skew fix's whole purpose is to push the short put further OTM when
realized downside vol exceeds what IV implied. These tests pin that
behavior so future regressions show up loudly.
"""
import math
import statistics

import pytest

from bloodaxe_pkg.expected_move.realized_vol import (
    compute_hv,
    compute_downside_hv,
    compute_downside_skew_ratio,
    skew_adjusted_em,
    adjusted_call_em,
)


# ---------------------------------------------------------------------------
# Test fixtures — synthetic price series
# ---------------------------------------------------------------------------

def _flat_series(n: int = 60, base: float = 100.0) -> list[float]:
    """No movement — hv=0, downside_hv=0."""
    return [base] * n


def _steady_uptrend(n: int = 60, daily_pct: float = 0.005,
                    daily_sigma: float = 0.01, seed: int = 7) -> list[float]:
    """Uptrend with realistic noise — small positive drift + Gaussian shocks.
    Downside moves happen but are smaller than upside on average."""
    import random
    random.seed(seed)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(random.gauss(daily_pct, daily_sigma)))
    return out


def _steady_downtrend(n: int = 60, daily_pct: float = -0.005,
                      daily_sigma: float = 0.01, seed: int = 13) -> list[float]:
    """Downtrend with realistic noise — persistent negative drift.
    Most moves are negative → dhv should be in the same magnitude as hv."""
    import random
    random.seed(seed)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(random.gauss(daily_pct, daily_sigma)))
    return out


def _mixed_normal_vol(n: int = 60, daily_sigma: float = 0.01, seed: int = 42) -> list[float]:
    """Symmetric normal returns — dhv should be ~half of hv."""
    import random
    random.seed(seed)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(random.gauss(0, daily_sigma)))
    return out


def _baba_like_downtrend(n: int = 60, seed: int = 2026) -> list[float]:
    """Real-world-style: persistent downtrend with occasional rallies.
    Mirrors BABA's actual 30-day pattern from Tiger paper bars
    (HV ~32%, DHV ~25%, ratio ~0.76 — DHV < HV, IV dominates).
    Used to test the case where the skew fix does NOT widen the band,
    because the IV already prices the left tail.
    """
    import random
    random.seed(seed)
    out = [180.0]
    for _ in range(n - 1):
        # Drift: -0.4%/day baseline, plus noise that's slightly skewed negative.
        daily = random.gauss(-0.004, 0.018)
        # Occasional positive outliers (rare).
        if random.random() < 0.05:
            daily += random.uniform(0.01, 0.03)
        out.append(out[-1] * math.exp(daily))
    return out


# ---------------------------------------------------------------------------
# compute_hv
# ---------------------------------------------------------------------------

def test_compute_hv_flat_returns_zero():
    closes = _flat_series(60)
    hv = compute_hv(closes, window=30)
    assert hv is not None
    assert math.isclose(hv, 0.0, abs_tol=1e-9)


def test_compute_hv_too_few_samples_returns_none():
    closes = [100.0, 101.0, 102.0]  # only 2 returns
    assert compute_hv(closes, window=30) is None


def test_compute_hv_negative_or_zero_close_returns_none():
    """Negative or zero close within the 30-day window → None."""
    # Put a -1.0 close INSIDE the last 31 closes (so it falls in the window).
    closes = [100.0] * 14 + [-1.0] + [102.0] * 30  # 45 total; last 31 has the -1
    assert compute_hv(closes, window=30) is None


def test_compute_hv_uptrend_positive():
    """Even a steady uptrend has SOME realized vol — small but nonzero."""
    closes = _steady_uptrend(60, daily_pct=0.01)
    hv = compute_hv(closes, window=30)
    assert hv is not None
    assert hv > 0
    # 1% drift + 1% noise σ → annualized HV roughly 15-25% range
    assert 0.10 < hv < 0.30


def test_compute_hv_uses_last_n_samples():
    """HV should use the most recent 30 returns, not the full 60."""
    # 50 flat days followed by 10 volatile days. HV should reflect volatility.
    flat = [100.0] * 51
    volatile = [100.0 * (1.02 ** (i % 2)) for i in range(10)]
    closes = flat + volatile
    hv = compute_hv(closes, window=30)
    assert hv is not None and hv > 0


# ---------------------------------------------------------------------------
# compute_downside_hv
# ---------------------------------------------------------------------------

def test_compute_downside_hv_uptrend_is_small():
    """In an uptrend, dhv is positive but smaller than hv."""
    closes = _steady_uptrend(60, daily_pct=0.01, daily_sigma=0.01)
    hv = compute_hv(closes, window=30)
    dhv = compute_downside_hv(closes, window=30)
    assert hv is not None and dhv is not None
    assert 0 <= dhv < hv


def test_compute_downside_hv_downtrend_close_to_hv():
    """In a sustained downtrend, dhv approaches hv (most variance is downside)."""
    closes = _steady_downtrend(60, daily_pct=-0.01, daily_sigma=0.01)
    hv = compute_hv(closes, window=30)
    dhv = compute_downside_hv(closes, window=30)
    assert hv is not None and dhv is not None
    assert hv > 0
    # dhv should be a large fraction of hv in a downtrend.
    assert dhv / hv > 0.6


def test_compute_downside_hv_baba_like_heavy_skew():
    """BABA-like series (sustained downtrend) should show dhv/hv high."""
    closes = _baba_like_downtrend(60)
    hv = compute_hv(closes, window=30)
    dhv = compute_downside_hv(closes, window=30)
    assert hv is not None and dhv is not None
    # Most of the variance is downside → ratio should be high.
    assert dhv / hv > 0.6


# ---------------------------------------------------------------------------
# compute_downside_skew_ratio
# ---------------------------------------------------------------------------

def test_skew_ratio_flat_is_undefined():
    closes = _flat_series(60)
    # Both hv and dhv are 0 — ratio is undefined (None).
    assert compute_downside_skew_ratio(closes, window=30) is None


def test_skew_ratio_uptrend_below_half():
    """In an uptrend, downside moves are smaller → ratio < 0.5."""
    closes = _steady_uptrend(60)
    ratio = compute_downside_skew_ratio(closes, window=30)
    assert ratio is not None
    assert 0 < ratio < 0.5


def test_skew_ratio_downtrend_above_half():
    """In a downtrend, downside moves are bigger → ratio > 0.5."""
    closes = _steady_downtrend(60)
    ratio = compute_downside_skew_ratio(closes, window=30)
    assert ratio is not None
    assert ratio > 0.5


def test_skew_ratio_baba_like_above_half():
    """BABA-like series: persistent negative drift → ratio should be high."""
    closes = _baba_like_downtrend(60)
    ratio = compute_downside_skew_ratio(closes, window=30)
    assert ratio is not None
    assert ratio > 0.5


# ---------------------------------------------------------------------------
# skew_adjusted_em — the core operator directive
# ---------------------------------------------------------------------------

def test_skew_adjusted_em_uses_max_of_dhv_and_iv():
    """When dhv > iv, the adjusted EM widens. When iv > dhv, it stays at iv."""
    # BABA-like: dhv ~ 0.27 (annualized), iv = 0.20 (understated)
    closes = _baba_like_downtrend(60)
    spot, iv, dte = 100.0, 0.20, 45
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_adj = skew_adjusted_em(spot, iv, closes, dte)
    assert em_adj is not None
    assert em_adj > em_iv, "downside-heavy realized vol should widen the put EM"


def test_skew_adjusted_em_does_not_shrink_when_iv_dominant():
    """If IV already prices the left tail (dhv < iv), don't make it looser.

    Use a low-vol uptrend (small dhv) with a high IV — em_adj == em_iv."""
    closes = _steady_uptrend(60, daily_pct=0.005, daily_sigma=0.005)  # low dhv
    spot, iv, dte = 100.0, 0.50, 45  # high IV dominates
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_adj = skew_adjusted_em(spot, iv, closes, dte)
    assert em_adj is not None
    # max(dhv, iv) == iv → em == em_iv
    assert math.isclose(em_adj, em_iv, rel_tol=1e-9)


def test_skew_adjusted_em_safety_multiplier_widens():
    closes = _baba_like_downtrend(60)
    spot, iv, dte = 100.0, 0.20, 45
    em_safe_1 = skew_adjusted_em(spot, iv, closes, dte, skew_safety=1.0)
    em_safe_125 = skew_adjusted_em(spot, iv, closes, dte, skew_safety=1.25)
    assert em_safe_1 is not None and em_safe_125 is not None
    assert math.isclose(em_safe_125 / em_safe_1, 1.25, rel_tol=1e-9)


def test_skew_adjusted_em_safety_below_one_rejected():
    closes = _baba_like_downtrend(60)
    with pytest.raises(ValueError):
        skew_adjusted_em(100.0, 0.20, closes, 45, skew_safety=0.5)


def test_skew_adjusted_em_insufficient_data_returns_none():
    """Fewer than window+1 closes → None, not crash."""
    assert skew_adjusted_em(100.0, 0.20, [100.0, 101.0, 102.0], 45) is None


def test_skew_adjusted_em_invalid_inputs_return_none():
    closes = _baba_like_downtrend(60)
    assert skew_adjusted_em(0, 0.20, closes, 45) is None
    assert skew_adjusted_em(100.0, 0, closes, 45) is None
    assert skew_adjusted_em(100.0, 0.20, closes, 0) is None
    assert skew_adjusted_em(-100.0, 0.20, closes, 45) is None


def test_skew_adjusted_em_baba_specific_scenario():
    """Pin BABA's real-data behavior: HV~32%, DHV~25%, IV~29%.
    Document the surprising finding: IV already prices the left tail (DHV<IV),
    so the skew fix doesn't widen the band on BABA's actual data.

    This is the operator-facing reality check — the BABA blow-ups are
    NOT fixable by skew adjustment alone; the underlying bug is the
    spot-source error (~$180 reported when actual is ~$110).
    See SKILL.md / bloodaxe-strategy entry on BABA spot bug."""
    spot, iv, dte = 180.0, 0.29, 45
    closes = _baba_like_downtrend(60)
    hv = compute_hv(closes, window=30)
    dhv = compute_downside_hv(closes, window=30)
    assert hv is not None and dhv is not None
    # BABA-like series has dhv < iv — the skew fix correctly does NOT widen.
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_adj = skew_adjusted_em(spot, iv, closes, dte)
    assert em_adj is not None
    assert math.isclose(em_adj, em_iv, rel_tol=1e-9), (
        "BABA-like data has dhv<iv; em_adj should equal em_iv "
        "(IV already prices left tail). Real BABA bug is spot source."
    )


def test_skew_adjusted_em_synthetic_dhv_dominant_widens():
    """Synthetic scenario: DHV > IV → adjusted EM MUST widen.
    This is the scenario the skew fix is designed to protect against,
    when a name has heavy realized downside vol not yet reflected in IV."""
    spot, iv, dte = 100.0, 0.10, 45
    closes = _steady_downtrend(60, daily_pct=-0.02, daily_sigma=0.005)
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_adj = skew_adjusted_em(spot, iv, closes, dte)
    assert em_adj is not None
    # In a heavy downtrend with low IV, dhv will dwarf iv → band widens.
    assert em_adj > em_iv


def test_skew_adjusted_em_high_dhv_iv_scenario_widens():
    """When realized downside vol EXCEEDS IV, the band MUST widen. This is
    the scenario the skew fix is designed to protect against."""
    spot, iv, dte = 100.0, 0.10, 45
    closes = _baba_like_downtrend(60)  # dhv ~ 0.27 here, much > 0.10
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_adj = skew_adjusted_em(spot, iv, closes, dte)
    assert em_adj is not None
    # dhv (0.27) >> iv (0.10) → adjusted EM should be ~2.7× the IV-based EM
    assert em_adj / em_iv > 2.0


# ---------------------------------------------------------------------------
# adjusted_call_em — upside counterpart
# ---------------------------------------------------------------------------

def test_adjusted_call_em_uses_max_of_hv_and_iv():
    closes = _mixed_normal_vol(60, daily_sigma=0.02)
    spot, iv, dte = 100.0, 0.05, 45  # iv very low
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_call_adj = adjusted_call_em(spot, iv, closes, dte)
    # Realized HV ~ 0.02 × √252 ≈ 31.7% — way above iv → widening expected
    assert em_call_adj is not None
    assert em_call_adj > em_iv


def test_adjusted_call_em_no_widening_when_iv_dominant():
    closes = _steady_uptrend(60, daily_pct=0.005)  # low HV
    spot, iv, dte = 100.0, 0.50, 45  # iv very high
    em_iv = spot * iv * math.sqrt(dte / 365.0)
    em_call_adj = adjusted_call_em(spot, iv, closes, dte)
    assert em_call_adj is not None
    assert math.isclose(em_call_adj, em_iv, rel_tol=1e-9)