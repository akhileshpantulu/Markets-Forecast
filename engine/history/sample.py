"""The estimation sample: which annual growth observations models may fit on.

Every Phase B estimator must take its data from `build_growth_sample` and pass
its fit through `influence_check`. This is the guardrail layer between raw
history and the model:

1. Level breaks (anomalies.py) remove the growth observations that contain the step.
2. Partial years and the first year are never used for growth.
3. COVID years are marked so estimators can run with and without them.
4. Leave-one-out influence flags any single year that moves a parameter by an
   outsized amount, so an undetected anomaly cannot silently drive the fit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.config import MarketConfig
from engine.history.anomalies import LevelBreak

GROWTH_MEASURES = ("demand", "supply", "adr", "occupancy", "revpar")
# Which breaks contaminate which growth measure (occupancy = demand / supply, RevPAR = occ x ADR).
CONTAMINATION = {
    "demand": {"demand", "supply"},
    "supply": {"supply"},
    "adr": {"adr"},
    "occupancy": {"demand", "supply"},
    "revpar": {"adr", "demand", "supply"},
}


def build_growth_sample(annual: pd.DataFrame, breaks: list[LevelBreak], cfg: MarketConfig) -> pd.DataFrame:
    """One row per year with log growth per measure, a use flag and a reason.

    use_<m>: True when the observation may enter estimation in the default
    specification. covid: True for configured COVID years; the default
    specification excludes them, the sensitivity run includes them.
    """
    a = annual.sort_values("year").reset_index(drop=True).copy()
    covid = set(cfg.estimation.covid_years)
    out = pd.DataFrame({"year": a["year"], "complete": a["complete"]})
    prev_complete = a["complete"].shift(1, fill_value=False).astype(bool)
    out["covid"] = out["year"].isin(covid)
    for m in GROWTH_MEASURES:
        out[f"g_{m}"] = np.log(a[m] / a[m].shift(1))
        use, reason = [], []
        for i, row in out.iterrows():
            r = ""
            if i == 0:
                r = "first year"
            elif not row["complete"] or not prev_complete.iloc[i]:
                r = "partial year"
            else:
                hits = [b for b in breaks if b.action == "exclude_growth" and b.measure in CONTAMINATION[m]
                        and int(row["year"]) in b.affected_years]
                if hits:
                    r = "; ".join(f"{b.measure} level break {b.start:%Y-%m}" for b in hits)
                elif row["covid"]:
                    r = "COVID window (included only in sensitivity run)"
            use.append(r == "")
            reason.append(r)
        out[f"use_{m}"] = use
        out[f"reason_{m}"] = reason
    return out


def usable(sample: pd.DataFrame, measure: str, include_covid: bool = False) -> pd.Series:
    """Growth series for estimation, indexed by year."""
    mask = sample[f"use_{measure}"]
    if include_covid:
        mask = mask | (sample["covid"] & sample[f"reason_{measure}"].str.startswith("COVID"))
    return sample.loc[mask, ["year", f"g_{measure}"]].set_index("year")[f"g_{measure}"]


@dataclass
class InfluenceResult:
    full: dict[str, float]
    table: pd.DataFrame          # one row per left-out year: param deltas and standardised influence
    flagged: list[str]           # human-readable flags

    def flagged_years(self) -> list[int]:
        return sorted(self.table.loc[self.table["flag"], "year"].astype(int).tolist())


INFLUENCE_LIMIT = 3.0


def influence_check(series: pd.Series, estimator: Callable[[pd.Series], dict[str, float]],
                    limit: float = INFLUENCE_LIMIT) -> InfluenceResult:
    """Leave-one-out influence of each observation on each fitted parameter.

    Standardised influence = |theta(-i) - theta| / typical shift of the other
    years, where the typical shift is the root mean square of the leave-one-out
    shifts of all other observations. With n observations of equal influence
    every value is near 1. A value above
    `limit` means one year moves the parameter several times more than a
    typical year does. Flagged years are reported, not removed: Phase B fits
    with and without them when any are flagged.
    """
    s = series.dropna()
    full = estimator(s)
    rows = []
    for year in s.index:
        est = estimator(s.drop(year))
        rows.append({"year": year, **{f"d_{k}": est[k] - full[k] for k in full}})
    t = pd.DataFrame(rows)
    flags = []
    t["flag"] = False
    for k in full:
        d = t[f"d_{k}"].to_numpy()
        sq = d ** 2
        n = len(d)
        others = np.sqrt((sq.sum() - sq) / max(n - 1, 1))
        z = np.divide(np.abs(d), others, out=np.zeros_like(d), where=others > 0)
        t[f"z_{k}"] = z
        hit = z > limit
        t["flag"] |= hit
        for _, r in t[hit].iterrows():
            flags.append(f"{int(r['year'])} moves {k} by {r[f'd_{k}']:+.4f} "
                         f"({r[f'z_{k}']:.1f} times a typical year)")
    return InfluenceResult(full, t, flags)


def mean_estimator(s: pd.Series) -> dict[str, float]:
    """Location only. Spread is meant to be driven by real shocks (2001, 2009, 2015),
    so leave-one-out influence on the standard deviation is not a useful anomaly signal."""
    return {"mean": float(s.mean())}
