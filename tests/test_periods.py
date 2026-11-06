from datetime import datetime

import pandas as pd
import pytest

from engine.ingest.periods import (
    PeriodParseError,
    days_in_month,
    fiscal_year,
    month_number,
    parse_month_period,
    parse_year_period,
)


@pytest.mark.parametrize("label,expected", [
    ("Sept 2026", "2026-09-01"),
    ("Sep 2026", "2026-09-01"),
    ("September 2026", "2026-09-01"),
    ("Jan 1998", "1998-01-01"),
    (" Dec. 2001 ", "2001-12-01"),
    (datetime(2024, 7, 15), "2024-07-01"),
])
def test_parse_month_period(label, expected):
    assert parse_month_period(label) == pd.Timestamp(expected)


@pytest.mark.parametrize("bad", ["Smarch 2020", "2020-13", "", None])
def test_parse_month_period_rejects(bad):
    with pytest.raises(PeriodParseError):
        parse_month_period(bad)


def test_year_and_month_helpers():
    assert parse_year_period(2019) == pd.Timestamp("2019-01-01")
    assert parse_year_period("2019") == pd.Timestamp("2019-01-01")
    assert month_number("Sept") == 9
    assert month_number(None) is None
    assert days_in_month(pd.Timestamp("2024-02-01")) == 29
    assert days_in_month(pd.Timestamp("2026-02-01")) == 28


def test_fiscal_year():
    assert fiscal_year(pd.Timestamp("2026-03-01"), 1) == 2026
    assert fiscal_year(pd.Timestamp("2026-03-01"), 4) == 2026
    assert fiscal_year(pd.Timestamp("2026-04-01"), 4) == 2027
