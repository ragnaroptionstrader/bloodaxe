"""Tests for Bloodaxe event screen (Rule 2)."""
from datetime import date
from bloodaxe_pkg.scanner.events import (
    has_earnings_within_days,
    has_fomc_within_days,
    passes_event_screen,
    next_event_window,
)


def test_earnings_within_window():
    today = date(2026, 9, 25)
    earnings = date(2026, 9, 28)  # 3 days out
    assert has_earnings_within_days(today, earnings, safe_days=7) is True
    assert has_earnings_within_days(today, earnings, safe_days=2) is False


def test_earnings_past():
    today = date(2026, 9, 25)
    past_earnings = date(2026, 9, 20)  # 5 days ago
    assert has_earnings_within_days(today, past_earnings, safe_days=7) is False


def test_no_earnings():
    today = date(2026, 9, 25)
    assert has_earnings_within_days(today, None) is False


def test_fomc_within_window():
    today = date(2026, 9, 25)
    fomc = [date(2026, 9, 16), date(2026, 10, 28)]  # one past, one future
    assert has_fomc_within_days(today, fomc, safe_days=3) is False  # 9/16 was 9 days ago
    # 10/28 is 33 days out, beyond 3-day window
    assert has_fomc_within_days(today, [date(2026, 9, 28)], safe_days=3) is True


def test_passes_when_no_events():
    today = date(2026, 9, 25)
    # No events in any window
    assert passes_event_screen(today) is True


def test_fails_when_earnings_close():
    today = date(2026, 9, 25)
    assert passes_event_screen(today, earnings_date=date(2026, 9, 30)) is False


def test_next_event_window_text():
    today = date(2026, 9, 25)
    text = next_event_window(today)
    assert "no major events" in text or "earnings" in text
