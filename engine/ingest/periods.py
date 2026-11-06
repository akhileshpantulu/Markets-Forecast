"""Period label parsing."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime

import pandas as pd

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_YEAR = re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{4})\s*$")
_YEAR = re.compile(r"^\s*(\d{4})\s*$")


class PeriodParseError(ValueError):
    pass


def month_number(label) -> int | None:
    """Month name or abbreviation to 1..12. None for empty input."""
    if label is None or (isinstance(label, float) and pd.isna(label)):
        return None
    if isinstance(label, (int, float)):
        m = int(label)
        if 1 <= m <= 12:
            return m
        raise PeriodParseError(f"month out of range: {label!r}")
    key = str(label).strip().lower().rstrip(".")
    if key == "":
        return None
    if key not in MONTHS:
        raise PeriodParseError(f"unknown month label: {label!r}")
    return MONTHS[key]


def parse_month_period(value) -> pd.Timestamp:
    """Parse 'Sept 2026', 'Jan 1998' or a date/datetime to the first of the month."""
    if isinstance(value, (datetime, date, pd.Timestamp)):
        ts = pd.Timestamp(value)
        return pd.Timestamp(year=ts.year, month=ts.month, day=1)
    if value is None:
        raise PeriodParseError("empty period")
    m = _MONTH_YEAR.match(str(value))
    if not m:
        raise PeriodParseError(f"unrecognised month period: {value!r}")
    month = month_number(m.group(1))
    return pd.Timestamp(year=int(m.group(2)), month=month, day=1)


def parse_year_period(value) -> pd.Timestamp:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return pd.Timestamp(year=int(value), month=1, day=1)
    m = _YEAR.match(str(value))
    if not m:
        raise PeriodParseError(f"unrecognised year period: {value!r}")
    return pd.Timestamp(year=int(m.group(1)), month=1, day=1)


def parse_month_year_string(value) -> pd.Timestamp | None:
    """Parse optional 'Mar 2026' style dates; None when empty."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and value.strip() in {"", "-"}:
        return None
    return parse_month_period(value)


def days_in_month(ts: pd.Timestamp) -> int:
    return calendar.monthrange(ts.year, ts.month)[1]


def fiscal_year(ts: pd.Timestamp, start_month: int = 1) -> int:
    """Fiscal year label = calendar year in which the fiscal year ends."""
    if start_month == 1:
        return ts.year
    return ts.year + 1 if ts.month >= start_month else ts.year
