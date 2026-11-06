"""Long-run anchor: the market's compound annual growth over its whole history.

User decision (2026-11-06): the baseline forecast is the long-run RevPAR CAGR,
adjusted for drivers, supply against its trend, inflation against its history,
shocks and momentum. RevPAR = occupancy x ADR, so its CAGR splits exactly into
an occupancy CAGR and an ADR CAGR; occupancy = demand / supply, so the demand
CAGR = supply CAGR + occupancy CAGR (in logs). Levels are the guarded (spliced)
annual series, so a data break cannot distort the endpoints.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Anchor:
    start: int
    end: int
    years: int
    revpar: float          # simple CAGRs (0.027 = 2.7% a year)
    occupancy: float
    adr: float
    supply: float
    demand: float
    cpi_mean: float | None  # geometric average inflation over (start, end]
    adr_real: float | None

    def log(self, name: str) -> float:
        v = getattr(self, name)
        return float(np.log1p(v)) if v is not None else 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def compute_anchor(annual: pd.DataFrame, start_year: int | None = None, end_year: int | None = None,
                   cpi: dict[int, float] | None = None) -> Anchor | None:
    """annual: HistoryResult.annual (complete years, spliced levels)."""
    a = annual.set_index("year").sort_index()
    if start_year is not None:
        a = a[a.index >= start_year]
    if end_year is not None:
        a = a[a.index <= end_year]
    if len(a) < 2:
        return None
    s, e = int(a.index[0]), int(a.index[-1])
    n = e - s

    def cagr(col):
        return float((a.loc[e, col] / a.loc[s, col]) ** (1 / n) - 1)

    cpi_mean = adr_real = None
    if cpi and all((y in cpi) for y in range(s + 1, e + 1)):
        cpi_mean = float(np.exp(np.mean([np.log1p(cpi[y]) for y in range(s + 1, e + 1)])) - 1)
        adr_real = (1 + cagr("adr")) / (1 + cpi_mean) - 1
    return Anchor(s, e, n, cagr("revpar"), cagr("occupancy"), cagr("adr"), cagr("supply"), cagr("demand"),
                  cpi_mean, adr_real)


def apply_anchor(dm, am, anchor: Anchor, cpi_mode: bool) -> dict:
    """Set the demand trend and ADR drift so the average path grows at the long-run CAGRs.

    Demand trend = long-run demand CAGR minus the expected shock drag (history's CAGR already
    contains its downturns, and the shock regime adds them back). ADR drift = long-run ADR CAGR,
    real in CPI mode (inflation is added each year). Pricing power acts on occupancy change
    beyond occupancy's own long-run trend. Returns the fitted values it replaced.
    """
    fitted = {"demand_mu": dm.mu, "adr_alpha": am.alpha}
    drag = dm.expected_shock_drag()
    dm.mu = anchor.log("demand") - drag
    if cpi_mode and anchor.cpi_mean is not None:
        am.alpha = anchor.log("adr") - float(np.log1p(anchor.cpi_mean))
    else:
        am.alpha = anchor.log("adr")
    am.occ_trend = anchor.log("occupancy")
    return {**fitted, "shock_drag": drag}


MIN_WINDOW = 5


def first_clean_year(breaks) -> int | None:
    """First calendar year whose levels need no splice: the year after the last confirmed break began
    (the same year when the break began in January)."""
    ys = [b.start.year + (0 if b.start.month == 1 else 1) for b in (breaks or []) if b.action == "exclude_growth"]
    return max(ys) if ys else None


def anchor_window(available_years: list[int], end: int, covid_years: set[int], start_year: int | None,
                  live_length: int | None, min_start: int | None = None) -> tuple[int, int] | None:
    """Choose (start, end) for an anchor ending at or before `end`.

    Years before min_start (levels touched by a data break) are never used as endpoints, so a splice
    correction cannot move the CAGR. The end moves back to the latest non-COVID year. With a user start year, the window keeps the live
    window's length (a rolling window), so a backtest origin never gets a one-year window. The start
    moves forward out of COVID years. Windows are at least MIN_WINDOW years when the data allow.
    """
    yrs = sorted(y for y in available_years if y <= end and y not in covid_years and (min_start is None or y >= min_start))
    if len(yrs) < 2:
        return None
    e = yrs[-1]
    first = yrs[0]
    if start_year is None or live_length is None:
        s = first
    else:
        s = max(first, e - live_length)
    while s in covid_years and s < e:
        s += 1
    s = min([y for y in yrs if y >= s] or [first])
    if e - s < MIN_WINDOW:
        s = max([y for y in yrs if y <= e - MIN_WINDOW] or [first])   # widen backward to a non-COVID year
    return (s, e) if e > s else None
