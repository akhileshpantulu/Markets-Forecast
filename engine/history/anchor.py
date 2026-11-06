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
