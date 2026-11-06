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
    notes: list[str] = field(default_factory=list)

    @property
    def supply(self) -> np.ndarray:
        return self.rooms * self.days[None, :]

    def induced_demand(self) -> np.ndarray:
        return self.induced_rooms * self.days[None, :] * self.ref_occupancy


def simulate_supply(pipeline: pd.DataFrame | None, monthly: pd.DataFrame, months: pd.DatetimeIndex,
                    priors: dict, n_paths: int, rng: np.random.Generator) -> SupplyPaths:
    last = monthly.sort_values("period_start").iloc[-1]
    base = float(last["available_rooms"])
    trailing = monthly.sort_values("period_start").tail(12)
    ref_occ = float(trailing["demand"].sum() / trailing["supply"].sum())
    days = np.array([pd.Timestamp(m).days_in_month for m in months], dtype=float)
    n_m = len(months)
    first = months[0]
    notes: list[str] = []

    # Removals: compound annual rate per path, applied to the starting stock.
    rate = _tri(rng, priors["removals"]["annual_rate"], n_paths)
    t_years = (np.arange(n_m) + 1) / 12.0
    rooms = base * (1 - rate[:, None]) ** t_years[None, :]

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
        rooms += mask * rooms_added
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
    return SupplyPaths(months, days, rooms, induced, open_month, pd.DataFrame(rows), base, ref_occ, rate, notes)


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
