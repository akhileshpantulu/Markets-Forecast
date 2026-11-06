"""Supply model: monthly available rooms per simulated path.

Starts from the latest month's available rooms (temporarily closed hotels are
out of supply), then for each pipeline project draws whether and when it opens
from stage priors (config/pipeline_priors.yaml), removes a share of the
existing stock each year, and adds induced demand from new and reopened rooms
with a ramp-up. numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

UNOPENED = {"under_construction", "under_renovation", "final_planning", "planning", "proposed", "deferred", "abandoned"}


def _tri(rng: np.random.Generator, spec: dict, size) -> np.ndarray:
    lo, mo, hi = spec["low"], spec["mode"], spec["high"]
    if hi <= lo:
        return np.full(size, float(mo))
    return rng.triangular(lo, mo, hi, size=size)


@dataclass
class SupplyPaths:
    months: pd.DatetimeIndex            # forecast months
    days: np.ndarray                    # days per month
    rooms: np.ndarray                   # available rooms [paths, months]
    induced_rooms: np.ndarray           # stabilised-equivalent rooms bringing new demand [paths, months]
    open_month: dict[str, np.ndarray]   # project_id -> month index of opening, -1 if not within horizon
    projects: pd.DataFrame
    base_rooms: float
    ref_occupancy: float
    removal_rate: np.ndarray
    components: dict[str, np.ndarray] = field(default_factory=dict)   # rooms by source [paths, months]
    seasonal_index: dict[int, float] = field(default_factory=dict)
    unannounced_rate: float = 0.0
    notes: list[str] = field(default_factory=list)
    unannounced_by_year: dict[int, float] = field(default_factory=dict)

    @property
    def supply(self) -> np.ndarray:
        return self.rooms * self.days[None, :]

    def induced_demand(self) -> np.ndarray:
        return self.induced_rooms * self.days[None, :] * self.ref_occupancy


SEASONAL_MIN_AMPLITUDE = 0.03


def seasonal_availability(monthly: pd.DataFrame, covid_years: set[int], years: int = 3) -> dict[int, float]:
    """Median ratio of each calendar month's available rooms to its year's average, over the last
    complete non-COVID years. Captures hotels that close seasonally. Returns all 1.0 when the
    pattern is weaker than SEASONAL_MIN_AMPLITUDE (a flat, year-round market)."""
    m = monthly.copy()
    m["year"] = m["period_start"].dt.year
    m["month"] = m["period_start"].dt.month
    counts = m.groupby("year")["month"].count()
    full = [y for y in counts.index if counts[y] == 12 and y not in covid_years][-years:]
    flat = {k: 1.0 for k in range(1, 13)}
    if not full:
        return flat
    x = m[m["year"].isin(full)].copy()
    x["ratio"] = x["available_rooms"] / x.groupby("year")["available_rooms"].transform("mean")
    idx = x.groupby("month")["ratio"].median()
    if idx.max() - idx.min() < SEASONAL_MIN_AMPLITUDE:
        return flat
    return {int(k): float(v) for k, v in idx.items()}


def simulate_supply(pipeline: pd.DataFrame | None, monthly: pd.DataFrame, months: pd.DatetimeIndex,
                    priors: dict, n_paths: int, rng: np.random.Generator, hist_supply_growth: float | None = None,
                    covid_years: list[int] | None = None) -> SupplyPaths:
    ms = monthly.sort_values("period_start")
    last = ms.iloc[-1]
    seas = seasonal_availability(ms, set(covid_years or []))
    # Level from the latest month, shape from the seasonal index (1.0 everywhere for year-round markets).
    base = float(last["available_rooms"]) / seas[int(last["period_start"].month)]
    trailing = ms.tail(12)
    ref_occ = float(trailing["demand"].sum() / trailing["supply"].sum())
    days = np.array([pd.Timestamp(m).days_in_month for m in months], dtype=float)
    season = np.array([seas[int(pd.Timestamp(m).month)] for m in months])
    n_m = len(months)
    first = months[0]
    notes: list[str] = []
    if any(abs(v - 1) > 1e-9 for v in seas.values()):
        notes.append("Seasonal closures detected: future months follow the recent seasonal pattern of available rooms "
                     f"(lowest month {min(seas.values()):.0%} of the yearly average, highest {max(seas.values()):.0%}).")

    # Removals: compound annual rate per path, applied to the starting stock.
    rate = _tri(rng, priors["removals"]["annual_rate"], n_paths)
    t_years = (np.arange(n_m) + 1) / 12.0
    stock = base * (1 - rate[:, None]) ** t_years[None, :]
    removed = base - stock
    new_rooms = np.zeros((n_paths, n_m))
    reopened = np.zeros((n_paths, n_m))

    induced = np.zeros((n_paths, n_m))
    ramp = np.asarray(priors["ramp_up"]["demand_share_by_year"], float)
    share_new = _tri(rng, priors["induced_demand"]["share_of_new_rooms"], n_paths)
    share_reo = _tri(rng, priors["induced_demand"]["share_of_reopened_rooms"], n_paths)
    open_month: dict[str, np.ndarray] = {}
    rows = []
    pipe = pipeline if pipeline is not None else pd.DataFrame()
    if not pipe.empty and pipe["snapshot_date"].nunique() > 1:
        pipe = pipe[pipe["snapshot_date"] == pipe["snapshot_date"].max()]
    for r in pipe.itertuples():
        if r.stage not in UNOPENED or r.project_type == "renovation_open":
            continue
        rooms_added = float(r.expansion_rooms) if r.project_type == "expansion" and not pd.isna(r.expansion_rooms) else float(r.rooms)
        st = priors["stages"].get(r.stage)
        if st is None:
            notes.append(f"No prior for stage {r.stage}; {r.name} not modelled.")
            continue
        opens = rng.random(n_paths) < st["p_open_within_horizon"]
        planned = pd.Timestamp(r.projected_open) if not pd.isna(r.projected_open) else first + pd.DateOffset(months=12)
        planned_idx = (planned.year - first.year) * 12 + planned.month - first.month
        planned_idx = max(planned_idx, 0)          # a date already passed means "late"
        delay = np.rint(_tri(rng, st["delay_months"], n_paths)).astype(int)
        idx = planned_idx + delay
        idx = np.where(opens & (idx < n_m), idx, -1)
        open_month[str(r.project_id)] = idx
        live = idx >= 0
        mask = live[:, None] & (np.arange(n_m)[None, :] >= idx[:, None])
        if r.project_type == "renovation_reopening":
            reopened += mask * rooms_added
        else:
            new_rooms += mask * rooms_added
        # Ramp: months since opening -> year index -> share of stabilised demand.
        since = np.arange(n_m)[None, :] - idx[:, None]
        yr = np.clip(since // 12, 0, len(ramp) - 1)
        share = share_reo if r.project_type == "renovation_reopening" else share_new
        induced += mask * rooms_added * ramp[yr] * share[:, None]
        rows.append({"project_id": str(r.project_id), "name": r.name, "rooms": rooms_added, "stage": r.stage,
                     "project_type": r.project_type,
                     "projected_open": None if pd.isna(r.projected_open) else pd.Timestamp(r.projected_open).strftime("%Y-%m"),
                     "p_open_horizon": float(live.mean()),
                     "median_open": months[int(np.median(idx[live]))].strftime("%Y-%m") if live.any() else None})

    # Supply floor at the market's long-run trend (user decision, 2026-11-06): the pipeline drives
    # supply, but it is not the only source. Each month, available rooms are the higher of
    # (a) the long-run supply trend from today's level and (b) existing stock plus the pipeline.
    # The gap is "unannounced" supply. Demand for trend-level supply is already in the demand
    # anchor (long-run demand CAGR), so only pipeline rooms above the trend induce extra demand.
    ua = priors.get("unannounced_supply", {})
    u_rate = 0.0
    trend = None
    unannounced = np.zeros((n_paths, n_m))
    u_by_year: dict[int, float] = {}
    pipe = new_rooms + reopened
    if ua.get("enabled", True) and hist_supply_growth is not None:
        u_rate = float(np.clip(hist_supply_growth, ua.get("min_rate", -0.05), ua.get("max_rate", 0.10)))
        share = ua.get("trend_share_by_year", [1.0, 1.0, 1.0, 1.0, 1.0])
        spread = float(ua.get("uncertainty", 0.5))
        r_path = u_rate * _tri(rng, {"low": 1 - spread, "mode": 1.0, "high": 1 + spread}, n_paths)
        stub_year = first.year if first.month > 1 else first.year - 1
        cum = np.zeros(n_paths)
        trend = np.empty((n_paths, n_m))
        for j, mth in enumerate(months):
            k = mth.year - stub_year - 1                      # 0 for the first full forecast year
            w = share[min(k, len(share) - 1)] if k >= 0 else 1.0
            cum = cum + np.log1p(r_path * w) / 12.0
            trend[:, j] = base * np.exp(cum)
        unannounced = np.maximum(0.0, trend - (stock + pipe))
        allowance = np.maximum(0.0, trend - stock)
        excess = np.maximum(0.0, pipe - allowance)
        frac = np.divide(excess, pipe, out=np.zeros_like(pipe), where=pipe > 0)
        induced = induced * frac
        month_years = np.array([m.year for m in months])
        prev = 0.0
        for y in sorted(set(month_years)):
            last_j = np.nonzero(month_years == y)[0][-1]
            end_u = float(unannounced[:, last_j].mean())
            u_by_year[int(y)] = (end_u - prev) / base
            prev = end_u
    rooms_flat = stock + new_rooms + reopened + unannounced
    rooms = rooms_flat * season[None, :]
    comps = {"stock_after_removals": stock * season[None, :], "removed": removed * season[None, :],
             "new_builds": new_rooms * season[None, :], "reopenings": reopened * season[None, :],
             "unannounced": unannounced * season[None, :]}
    induced = induced * season[None, :]
    sp = SupplyPaths(months, days, rooms, induced, open_month, pd.DataFrame(rows), base, ref_occ, rate, comps, seas,
                     u_rate, notes)
    sp.unannounced_by_year = u_by_year
    return sp


def annual_open_probabilities(sp: SupplyPaths) -> pd.DataFrame:
    """P(project open by the end of each forecast year)."""
    years = sorted(set(sp.months.year))
    out = []
    for pid, idx in sp.open_month.items():
        row = {"project_id": pid}
        for y in years:
            last_idx = max(i for i, m in enumerate(sp.months) if m.year == y)
            row[str(y)] = float(((idx >= 0) & (idx <= last_idx)).mean())
        out.append(row)
    return pd.DataFrame(out)
