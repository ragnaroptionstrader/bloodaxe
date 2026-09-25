"""Bloodaxe event filter — Rule 2.

Blacklist dates with binary catalysts that could gap the underlying
beyond the IC's short strikes:
- Earnings (within 7 days)
- FOMC meetings (within 3 days)
- CPI releases (within 1 day)
- NFP / jobs report (within 1 day)

Returns True if a date is "safe" to sell premium.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable


def has_earnings_within_days(today: date, earnings_date: date | None,
                              safe_days: int = 7) -> bool:
    """Return True if earnings fall within `safe_days` of today."""
    if not earnings_date:
        return False
    delta = (earnings_date - today).days
    return 0 <= delta <= safe_days


def has_fomc_within_days(today: date, fomc_dates: Iterable[date],
                          safe_days: int = 3) -> bool:
    """Return True if any FOMC date falls within `safe_days`."""
    for f in fomc_dates:
        delta = (f - today).days
        if 0 <= delta <= safe_days:
            return True
    return False


def has_cpi_within_days(today: date, cpi_dates: Iterable[date],
                         safe_days: int = 1) -> bool:
    """Return True if any CPI release falls within `safe_days`."""
    for c in cpi_dates:
        delta = (c - today).days
        if 0 <= delta <= safe_days:
            return True
    return False


def has_nfp_within_days(today: date, nfp_dates: Iterable[date],
                        safe_days: int = 1) -> bool:
    """Return True if any NFP release falls within `safe_days`."""
    for n in nfp_dates:
        delta = (n - today).days
        if 0 <= delta <= safe_days:
            return True
    return False


# ---------------------------------------------------------------------------
# Hardcoded 2026 calendar (refresh quarterly; replace with API lookup later)
# ---------------------------------------------------------------------------

FOMC_2026 = [
    # Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    date(2026, 1, 28), date(2026, 1, 29),  # Jan
    date(2026, 3, 18),                       # Mar
    date(2026, 4, 29),                       # Apr
    date(2026, 6, 17),                       # Jun
    date(2026, 7, 29),                       # Jul
    date(2026, 9, 16),                       # Sep
    date(2026, 10, 28),                      # Oct
    date(2026, 12, 16),                      # Dec
]

CPI_2026 = [
    # Source: https://www.bls.gov/schedule/news_release/cpi.htm
    date(2026, 1, 13), date(2026, 2, 12), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 13), date(2026, 6, 11),
    date(2026, 7, 14), date(2026, 8, 13), date(2026, 9, 16),
    date(2026, 10, 14), date(2026, 11, 13), date(2026, 12, 11),
]

NFP_2026 = [
    # First Friday of each month (typical BLS release schedule)
    date(2026, 1, 9),  date(2026, 2, 6),  date(2026, 3, 6),
    date(2026, 4, 3),  date(2026, 5, 8),  date(2026, 6, 5),
    date(2026, 7, 2),  date(2026, 8, 7),  date(2026, 9, 4),
    date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4),
]


def passes_event_screen(
    today: date,
    *,
    earnings_date: date | None = None,
    fomc_dates: Iterable[date] = FOMC_2026,
    cpi_dates: Iterable[date] = CPI_2026,
    nfp_dates: Iterable[date] = NFP_2026,
    earnings_safe_days: int = 7,
    fomc_safe_days: int = 3,
    cpi_safe_days: int = 1,
    nfp_safe_days: int = 1,
) -> bool:
    """Return True if NO risky event falls within the safe windows."""
    if has_earnings_within_days(today, earnings_date, earnings_safe_days):
        return False
    if has_fomc_within_days(today, fomc_dates, fomc_safe_days):
        return False
    if has_cpi_within_days(today, cpi_dates, cpi_safe_days):
        return False
    if has_nfp_within_days(today, nfp_dates, nfp_safe_days):
        return False
    return True


def next_event_window(today: date,
                       earnings_date: date | None = None,
                       fomc_dates: Iterable[date] = FOMC_2026,
                       cpi_dates: Iterable[date] = CPI_2026,
                       nfp_dates: Iterable[date] = NFP_2026) -> str:
    """Return a human-readable description of the next event risk."""
    events = []
    if earnings_date:
        delta = (earnings_date - today).days
        if delta >= 0:
            events.append(f"earnings in {delta}d")
    for f in fomc_dates:
        delta = (f - today).days
        if 0 <= delta <= 7:
            events.append(f"FOMC in {delta}d")
            break
    for c in cpi_dates:
        delta = (c - today).days
        if 0 <= delta <= 3:
            events.append(f"CPI in {delta}d")
            break
    for n in nfp_dates:
        delta = (n - today).days
        if 0 <= delta <= 3:
            events.append(f"NFP in {delta}d")
            break
    return ", ".join(events) if events else "no major events in window"
