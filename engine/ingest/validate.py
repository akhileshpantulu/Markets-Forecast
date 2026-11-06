"""Validation checks for performance and pipeline data.

Levels:
  hard: a failure stops the run
  soft: a failure is reported as a warning and the run continues
  info: descriptive facts about the data
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from engine.config import MarketConfig
from engine.ingest.periods import days_in_month
from engine.schema import PERFORMANCE_CORE, PerformanceRecord, validate_frame

Level = Literal["hard", "soft", "info"]
Status = Literal["pass", "warn", "fail", "info"]

CORE_NUMERIC = ["supply", "demand", "revenue", "occupancy", "adr", "revpar"]
REL_TOL = 1e-6


@dataclass
class Check:
    id: str
    level: Level
    status: Status
    title: str
    message: str
    details: list[str] = field(default_factory=list)

    @property
    def failed_hard(self) -> bool:
        return self.level == "hard" and self.status == "fail"


def _check(cid, level, ok, title, msg_ok, msg_bad, details=None) -> Check:
    if ok:
        status = "pass" if level != "info" else "info"
        return Check(cid, level, status, title, msg_ok, details or [])
    status = "fail" if level == "hard" else ("warn" if level == "soft" else "info")
    return Check(cid, level, status, title, msg_bad, details or [])


def _fmt_month(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


def split_performance_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split into (complete performance rows, inventory-only rows, partial rows)."""
    present = df[CORE_NUMERIC].notna()
    complete = present.all(axis=1)
    none = ~present.any(axis=1)
    partial = ~(complete | none)
    return df[complete].copy(), df[none].copy(), df[partial].copy()


def add_derived_columns(perf: pd.DataFrame) -> pd.DataFrame:
    perf = perf.sort_values("period_start").reset_index(drop=True)
    perf["days_in_period"] = perf["period_start"].map(days_in_month).astype("int64")
    perf["available_rooms"] = perf["supply"] / perf["days_in_period"]
    perf["closed_rooms"] = perf["inventory_rooms"] - perf["available_rooms"]
    return perf


def validate_performance(raw_df: pd.DataFrame, xc: pd.DataFrame, conversion_errors: list[str],
                         cfg: MarketConfig) -> tuple[list[Check], pd.DataFrame]:
    """Run checks; return (checks, cleaned monthly performance frame)."""
    checks: list[Check] = []

    checks.append(_check("P01", "hard", not conversion_errors, "Values parse",
                         "All periods and numeric values parsed.",
                         f"{len(conversion_errors)} values could not be parsed.", conversion_errors[:20]))

    dups = raw_df["period_start"][raw_df["period_start"].duplicated(keep=False)].dropna()
    checks.append(_check("P02", "hard", dups.empty, "No duplicate months",
                         "Each month appears once.",
                         f"{dups.nunique()} months appear more than once.",
                         sorted({_fmt_month(d) for d in dups})))
    if not dups.empty:
        # Later checks assume one row per month; stop here.
        return checks, raw_df.iloc[0:0].copy()

    perf, inv_only, partial = split_performance_rows(raw_df)
    checks.append(_check("P03", "hard", partial.empty, "No partial rows",
                         "Every row has either all or none of supply, demand, revenue, occupancy, ADR, RevPAR.",
                         f"{len(partial)} rows have some but not all core measures.",
                         [_fmt_month(p) for p in partial["period_start"]]))
    if not inv_only.empty:
        checks.append(Check("P04", "info", "info", "Inventory-only rows excluded",
                            f"{len(inv_only)} months carry inventory but no performance data "
                            f"({_fmt_month(inv_only['period_start'].min())} to {_fmt_month(inv_only['period_start'].max())}). "
                            "They are excluded from performance history."))

    if perf.empty:
        checks.append(Check("P05", "hard", "fail", "Performance data present", "No complete performance rows."))
        return checks, perf

    perf = add_derived_columns(perf)
    start, end = perf["period_start"].min(), perf["period_start"].max()
    expected = pd.date_range(start, end, freq="MS")
    missing = expected.difference(pd.DatetimeIndex(perf["period_start"]))
    checks.append(_check("P05", "hard", len(missing) == 0, "Monthly continuity",
                         f"{len(perf)} consecutive months, {_fmt_month(start)} to {_fmt_month(end)}.",
                         f"{len(missing)} months missing between {_fmt_month(start)} and {_fmt_month(end)}.",
                         [_fmt_month(m) for m in missing[:24]]))

    neg = perf[(perf[["supply", "demand", "revenue", "adr", "revpar"]] < 0).any(axis=1)]
    occ_bad = perf[(perf["occupancy"] < 0) | (perf["occupancy"] > 1)]
    bad = pd.concat([neg, occ_bad]).drop_duplicates("period_start")
    checks.append(_check("P06", "hard", bad.empty, "Ranges",
                         "No negative values; occupancy within 0 to 1.",
                         f"{len(bad)} months out of range.", [_fmt_month(p) for p in bad["period_start"]]))

    def identity(cid, title, lhs, rhs, label):
        rel = (lhs - rhs).abs() / rhs.abs().where(rhs.abs() > 0, 1.0)
        worst = float(rel.max())
        offenders = perf.loc[rel > REL_TOL, "period_start"]
        return _check(cid, "hard", offenders.empty, title,
                      f"{label} holds for every month (max relative error {worst:.1e}).",
                      f"{label} fails in {len(offenders)} months (max relative error {worst:.1e}).",
                      [_fmt_month(p) for p in offenders[:24]])

    checks.append(identity("P07", "Occupancy identity", perf["demand"] / perf["supply"], perf["occupancy"],
                           "Demand / supply = occupancy"))
    pos = perf["demand"] > 0
    checks.append(identity("P08", "ADR identity", (perf["revenue"] / perf["demand"]).where(pos, perf["adr"]),
                           perf["adr"], "Revenue / demand = ADR"))
    checks.append(identity("P09", "RevPAR identity", perf["occupancy"] * perf["adr"], perf["revpar"],
                           "Occupancy x ADR = RevPAR"))

    errs = validate_frame(perf[PERFORMANCE_CORE], PerformanceRecord, PERFORMANCE_CORE)
    checks.append(_check("P10", "hard", not errs, "Schema", "All rows conform to the internal schema.",
                         f"{len(errs)} rows fail schema validation.", errs[:10]))

    checks.append(_crosscheck_rolling(perf, xc))
    checks.append(_crosscheck_yoy(perf, xc))

    over = perf[perf["available_rooms"] > perf["inventory_rooms"] * (1 + 1e-3)]
    checks.append(_check("P13", "soft", over.empty, "Supply within inventory",
                         "Available rooms (supply / days) never exceed inventory rooms.",
                         f"Available rooms exceed inventory in {len(over)} months.",
                         [_fmt_month(p) for p in over["period_start"]]))

    ratio = perf["available_rooms"] / perf["inventory_rooms"]
    closed = perf[ratio < cfg.ingest.closure_flag_ratio]
    last = perf.iloc[-1]
    checks.append(Check(
        "P14", "info", "info", "Temporary closures",
        f"Latest month {_fmt_month(last['period_start'])}: inventory {last['inventory_rooms']:,.0f} rooms, "
        f"available {last['available_rooms']:,.0f}, so {last['closed_rooms']:,.0f} rooms are out of supply "
        f"(temporarily closed). {len(closed)} months have available rooms below "
        f"{cfg.ingest.closure_flag_ratio:.0%} of inventory.",
        [f"{_fmt_month(r.period_start)}: {r.available_rooms / r.inventory_rooms:.1%} of inventory available"
         for r in closed.itertuples()][:40]))

    checks.append(_outliers(perf, cfg))
    checks.append(_unit_consistency(perf, cfg))
    checks.append(_level_breaks(perf, cfg))

    ytd = perf[perf["period_start"].dt.year == end.year]
    checks.append(Check(
        "P17", "info", "info", "Stub year coverage",
        f"Latest actual month is {_fmt_month(end)}. The {end.year} stub year has {len(ytd)} actual months "
        f"and {12 - len(ytd)} months to simulate."))
    return checks, perf


def _crosscheck_rolling(perf: pd.DataFrame, xc: pd.DataFrame) -> Check:
    x = xc.set_index("period_start")
    s = perf.set_index("period_start")
    details, worst, n = [], 0.0, 0
    for measure in ("supply", "demand", "revenue"):
        col = f"rolling_12m_sum:{measure}"
        if col not in x.columns:
            continue
        rolled = s[measure].rolling(12).sum()
        ref = x[col].reindex(rolled.index)
        mask = rolled.notna() & ref.notna()
        rel = ((rolled[mask] - ref[mask]).abs() / ref[mask].abs()).fillna(0)
        n += int(mask.sum())
        if len(rel):
            worst = max(worst, float(rel.max()))
            bad = rel[rel > 1e-4]
            details += [f"{measure} {_fmt_month(p)}: relative difference {v:.2e}" for p, v in bad.items()][:10]
    return _check("P11", "soft", not details, "12-month totals reconcile",
                  f"Sums of 12 monthly values match CoStar's 12-month columns ({n} comparisons, max relative difference {worst:.1e}).",
                  f"{len(details)} 12-month totals differ from the sum of monthly values.", details)


def _crosscheck_yoy(perf: pd.DataFrame, xc: pd.DataFrame) -> Check:
    x = xc.set_index("period_start")
    s = perf.set_index("period_start")
    details, worst, n = [], 0.0, 0
    for measure in ("occupancy", "adr", "revpar", "supply", "demand"):
        col = f"yoy_relative:{measure}"
        if col not in x.columns:
            continue
        prior = s[measure].copy()
        prior.index = prior.index + pd.DateOffset(years=1)
        yoy = s[measure] / prior.reindex(s.index) - 1
        ref = x[col].reindex(s.index)
        mask = yoy.notna() & ref.notna() & np.isfinite(yoy)
        diff = (yoy[mask] - ref[mask]).abs()
        n += int(mask.sum())
        if len(diff):
            worst = max(worst, float(diff.max()))
            bad = diff[diff > 1e-4]
            details += [f"{measure} {_fmt_month(p)}: absolute difference {v:.2e}" for p, v in bad.items()][:10]
    return _check("P12", "soft", not details, "Year-over-year columns reconcile",
                  f"Computed YoY changes match CoStar's YoY columns ({n} comparisons, max absolute difference {worst:.1e}).",
                  f"{len(details)} YoY values differ from those computed from monthly levels.", details)


def _outliers(perf: pd.DataFrame, cfg: MarketConfig) -> Check:
    s = perf.set_index("period_start")
    covid = set(cfg.estimation.covid_years)
    flagged = []
    for measure in ("demand", "adr"):
        prior = s[measure].copy()
        prior.index = prior.index + pd.DateOffset(years=1)
        g = np.log(s[measure] / prior.reindex(s.index)).replace([np.inf, -np.inf], np.nan).dropna()
        med = g.median()
        mad = (g - med).abs().median() * 1.4826
        if mad == 0:
            continue
        z = (g - med) / mad
        for p, v in z[z.abs() > cfg.ingest.outlier_robust_z].items():
            tag = " (in configured COVID years)" if p.year in covid or p.year - 1 in covid else ""
            flagged.append((p, f"{measure} {_fmt_month(p)}: YoY log change {g[p]:+.3f}, robust z {v:+.1f}{tag}"))
    flagged.sort()
    non_covid = [d for p, d in flagged if "COVID" not in d]
    return Check("P15", "soft", "warn" if non_covid else "pass", "Outliers",
                 f"{len(flagged)} monthly YoY changes exceed robust z {cfg.ingest.outlier_robust_z:g}; "
                 f"{len(non_covid)} fall outside the configured COVID years. Outliers are kept and reported, not removed.",
                 [d for _, d in flagged][:40])


def _unit_consistency(perf: pd.DataFrame, cfg: MarketConfig) -> Check:
    annual = perf.groupby(perf["period_start"].dt.year).agg(revenue=("revenue", "sum"), demand=("demand", "sum"))
    adr = annual["revenue"] / annual["demand"]
    jumps = (adr / adr.shift(1)).dropna()
    bad = jumps[(jumps < 0.5) | (jumps > 2.0)]
    return _check("P16", "soft", bad.empty, "Unit and currency consistency",
                  f"Annual ADR never halves or doubles year on year, consistent with one currency ({cfg.currency}) "
                  "throughout. The export does not state its currency; it is taken from the market config.",
                  f"Annual ADR halves or doubles in {len(bad)} years; check for a currency or unit change.",
                  [f"{y}: ratio {v:.2f}" for y, v in bad.items()])


BREAK_MEDIAN_YOY = 0.25


def _level_breaks(perf: pd.DataFrame, cfg: MarketConfig) -> Check:
    """Flag calendar years where nearly every month moves the same large amount.

    A one-off step in all twelve months (rather than a gradual trend or a shock
    that fades) usually means a change in sample, definition or unit. Years in
    the configured COVID window are excluded.
    """
    s = perf.set_index("period_start")
    covid = set(cfg.estimation.covid_years)
    details = []
    for measure in ("adr", "demand", "supply"):
        prior = s[measure].copy()
        prior.index = prior.index + pd.DateOffset(years=1)
        yoy = (s[measure] / prior.reindex(s.index) - 1).dropna()
        for year, g in yoy.groupby(yoy.index.year):
            if year in covid or len(g) < 12:
                continue
            med = float(g.median())
            same_sign = int((np.sign(g) == np.sign(med)).sum())
            if abs(med) > BREAK_MEDIAN_YOY and same_sign >= 11:
                details.append(f"{measure} {year}: median monthly YoY change {med:+.1%}, "
                               f"{same_sign} of 12 months move the same way (min {g.min():+.1%}, max {g.max():+.1%})")
    return _check("P18", "soft", not details, "Series level breaks",
                  f"No non-COVID year shows a uniform step above {BREAK_MEDIAN_YOY:.0%} in ADR, demand or supply.",
                  f"{len(details)} possible level breaks. Decide whether to exclude the affected growth "
                  "observations from parameter estimation (set estimation.series_breaks in the market config).",
                  details)


def validate_pipeline(pipe: pd.DataFrame, conversion_errors: list[str], perf: pd.DataFrame,
                      cfg: MarketConfig, snapshot_date) -> list[Check]:
    checks: list[Check] = []
    checks.append(_check("L01", "hard", not conversion_errors, "Values parse",
                         "All pipeline values parsed.", f"{len(conversion_errors)} values could not be parsed.",
                         conversion_errors[:20]))
    if pipe.empty:
        checks.append(Check("L02", "soft", "warn", "Projects present", "The pipeline export has no rows."))
        return checks
    dup = pipe[pipe["project_id"].duplicated(keep=False)]
    checks.append(_check("L02", "hard", dup.empty, "Unique project IDs", f"{len(pipe)} projects, all IDs unique.",
                         f"{dup['project_id'].nunique()} project IDs repeat.", list(dup["project_id"].unique())))
    bad_rooms = pipe[pipe["rooms"].isna() | (pipe["rooms"] <= 0)]
    checks.append(_check("L03", "hard", bad_rooms.empty, "Room counts", "Every project has a positive room count.",
                         f"{len(bad_rooms)} projects lack a positive room count.", list(bad_rooms["name"])))

    mk_bad = pipe[(cfg.costar_market is not None) & (pipe["market"] != cfg.costar_market)]
    sm_bad = pipe[(cfg.costar_submarket is not None) & (pipe["submarket"] != cfg.costar_submarket)]
    off = pd.concat([mk_bad, sm_bad]).drop_duplicates("project_id")
    checks.append(_check("L04", "soft", off.empty, "Geography matches config",
                         f"All projects are in {cfg.costar_market} / {cfg.costar_submarket}.",
                         f"{len(off)} projects sit outside the configured geography.",
                         [f"{r.name}: {r.market} / {r.submarket}" for r in off.itertuples()]))

    missing = pipe[pipe["projected_open_basis"] == "missing"]
    defaulted = pipe[pipe["projected_open_basis"] == "year_only_default_month"]
    checks.append(_check("L05", "soft", missing.empty and defaulted.empty, "Projected opening dates",
                         "Every project has a projected opening year and month.",
                         f"{len(missing)} projects have no projected opening; {len(defaulted)} have a year only "
                         "(month set to the configured default, an assumption).",
                         [f"{n}: missing" for n in missing["name"]] + [f"{n}: year only" for n in defaulted["name"]]))

    if snapshot_date is not None:
        snap = pd.Timestamp(snapshot_date)
        past = pipe[pipe["projected_open"].notna() & (pipe["projected_open"] < snap.replace(day=1))
                    & pipe["stage"].isin(["under_construction", "under_renovation", "final_planning", "planning", "proposed"])]
        checks.append(_check("L06", "soft", past.empty, "Opening dates in the future",
                             "No unopened project has a projected opening before the snapshot date.",
                             f"{len(past)} unopened projects have a projected opening before {snap.date()}; treat as delayed.",
                             [f"{r.name}: {r.projected_open:%Y-%m} ({r.stage})" for r in past.itertuples()]))

    by_stage = pipe.groupby(["stage", "project_type"])["rooms"].agg(["count", "sum"]).reset_index()
    checks.append(Check("L07", "info", "info", "Pipeline by stage",
                        f"{int(pipe['rooms'].sum())} rooms across {len(pipe)} projects.",
                        [f"{r.stage} / {r.project_type}: {r.count} projects, {int(r.sum)} rooms" for r in by_stage.itertuples()]))

    if perf is not None and not perf.empty:
        last = perf.sort_values("period_start").iloc[-1]
        closed_pipe = int(pipe.loc[pipe["project_type"] == "renovation_reopening", "rooms"].sum())
        gap = float(last["closed_rooms"])
        ok = abs(gap - closed_pipe) <= max(5.0, 0.05 * max(gap, 1.0))
        checks.append(_check(
            "L08", "soft", ok, "Closed rooms reconcile with pipeline",
            f"Rooms out of supply in {last['period_start']:%Y-%m} ({gap:,.0f}) match temporarily closed "
            f"renovation projects in the pipeline ({closed_pipe:,}). Supply excludes these hotels until they reopen.",
            f"Rooms out of supply in {last['period_start']:%Y-%m} ({gap:,.0f}) differ from temporarily closed "
            f"renovation projects in the pipeline ({closed_pipe:,}). Some closures may be missing from the export."))
    return checks
