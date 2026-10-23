"""Billing period arithmetic (SUB-02.4, SUB-02.5).

Billing is in advance with no proration. A billing anchor of day 29-31 clamps
to the last day of shorter months and returns to the anchor afterwards:
anchor 31 gives Jan 31, Feb 28, Mar 31, Apr 30, ...
"""

import calendar
from datetime import date, timedelta

RECURRENCE_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def anchored_date(year: int, month: int, anchor_day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor_day, last))


def add_months(start: date, months: int, anchor_day: int) -> date:
    """Move `months` forward from `start`, landing on the anchor day (clamped)."""
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    return anchored_date(year, month, anchor_day)


def next_period_start(period_start: date, recurrence: str, anchor_day: int) -> date:
    try:
        months = RECURRENCE_MONTHS[recurrence]
    except KeyError as exc:
        raise ValueError(f"Unknown recurrence: {recurrence!r}") from exc
    return add_months(period_start, months, anchor_day)


def period_for(period_start: date, recurrence: str, anchor_day: int) -> tuple[date, date]:
    """The (start, end) of the period beginning on `period_start`; end is inclusive."""
    nxt = next_period_start(period_start, recurrence, anchor_day)
    return period_start, nxt - timedelta(days=1)


def domain_period_for(period_start: date, years: int, anchor_day: int) -> tuple[date, date]:
    nxt = add_months(period_start, 12 * years, anchor_day)
    return period_start, nxt - timedelta(days=1)
