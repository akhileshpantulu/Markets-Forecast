"""Historical analysis: the context every forecast year is positioned against.

Uses the guarded history: level statistics use the spliced monthly series
(pre-break levels rescaled), growth statistics use only observations the
estimation sample allows (no break-contaminated years, no partial years).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.config import MarketConfig

PCTS = (5, 10, 25, 50, 75, 90, 95)

# Named episodes. A label is shown only when the data covers those years.
EPISODES = [
    ((2001, 2003), "2001 to 2003 downturn"),
    ((2008, 2009), "Global financial crisis (2008 to 2009)"),
    ((2011, 2013), "Euro area crisis years (2011 to 2013)"),
    ((2015, 2016), "2015 to 2016 downturn"),
    ((2020, 2022), "COVID-19 (2020 to 2022)"),
]


@dataclass
class HistoryResult:
    annual: pd.DataFrame                    # levels and growth by complete year (spliced levels)
    growth_pcts: pd.DataFrame               # percentiles of annual growth by measure, two samples
    cycles: pd.DataFrame                    # drawdown episodes
    decomposition: pd.DataFrame             # occupancy change split into demand and supply
    seasonality: pd.DataFrame               # monthly demand share and ADR index, early vs recent
    seasonality_shift: dict
    ceiling: dict                           # occupancy ceiling and its basis
    occ_annual_max: float
    occ_annual_min: float
    real_adr: bool
    notes: list[str] = field(default_factory=list)


def _annual_from_monthly(m: pd.DataFrame) -> pd.DataFrame:
    g = m.groupby(m["period_start"].dt.year)
    a = pd.DataFrame({"supply": g["supply"].sum(), "demand": g["demand"].sum(), "revenue": g["revenue"].sum(),
                      "months": g["period_start"].count()})
    a["occupancy"] = a["demand"] / a["supply"]
    a["adr"] = a["revenue"] / a["demand"]
    a["revpar"] = a["revenue"] / a["supply"]
    a.index.name = "year"
    return a.reset_index()


def build_annual(monthly_spliced: pd.DataFrame, sample: pd.DataFrame, cpi: dict[int, float] | None) -> pd.DataFrame:
    a = _annual_from_monthly(monthly_spliced)
    a = a[a["months"] == 12].reset_index(drop=True)
    s = sample.set_index("year")
    a["occ_growth"] = a["occupancy"] / a["occupancy"].shift(1) - 1
    a["occ_pts"] = a["occupancy"] - a["occupancy"].shift(1)
    a["adr_growth"] = a["adr"] / a["adr"].shift(1) - 1
    a["revpar_growth"] = a["revpar"] / a["revpar"].shift(1) - 1
    a["demand_growth"] = a["demand"] / a["demand"].shift(1) - 1
    a["supply_growth"] = a["supply"] / a["supply"].shift(1) - 1
    consecutive = a["year"].diff() == 1
    for c in ("occ_growth", "occ_pts", "adr_growth", "revpar_growth", "demand_growth", "supply_growth"):
        a.loc[~consecutive, c] = np.nan
    a["use_adr"] = a["year"].map(s["use_adr"]).fillna(False).astype(bool)
    a["use_occ"] = a["year"].map(s["use_occupancy"]).fillna(False).astype(bool)
    a["use_demand"] = a["year"].map(s["use_demand"]).fillna(False).astype(bool)
    a["covid"] = a["year"].map(s["covid"]).fillna(False).astype(bool)
    if cpi:
        a["cpi"] = a["year"].map(cpi)
        a["real_adr_growth"] = (1 + a["adr_growth"]) / (1 + a["cpi"]) - 1
    return a


def growth_percentiles(a: pd.DataFrame) -> pd.DataFrame:
    """Percentiles of annual growth. 'all' keeps COVID years, 'ex_covid' drops them.
    Break-contaminated observations are always dropped."""
    rows = []
    specs = [("occ_growth", "use_occ"), ("occ_pts", "use_occ"), ("adr_growth", "use_adr"),
             ("revpar_growth", "use_adr"), ("demand_growth", "use_demand"), ("supply_growth", None)]
    if "real_adr_growth" in a:
        specs.append(("real_adr_growth", "use_adr"))
    for measure, flag in specs:
        base = a[a[measure].notna()]
        if flag:
            # 'use_*' is False for COVID years too; rebuild the all-years mask from break status only.
            ok_break = base[flag] | base["covid"]
            base = base[ok_break]
        for sample, sub in (("all", base), ("ex_covid", base[~base["covid"]])):
            v = sub[measure].to_numpy()
            if len(v) == 0:
                continue
            row = {"measure": measure, "sample": sample, "n": len(v), "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else np.nan,
                   "min": float(v.min()), "max": float(v.max())}
            row.update({f"p{p}": float(np.percentile(v, p)) for p in PCTS})
            rows.append(row)
    return pd.DataFrame(rows)


def cycle_map(a: pd.DataFrame, measure: str = "revpar") -> pd.DataFrame:
    """Drawdowns of an annual level series: peak, trough, depth, recovery."""
    s = a.set_index("year")[measure]
    rows, peak_year, peak_val, in_dd, trough_year, trough_val = [], s.index[0], s.iloc[0], False, None, None
    for y, v in s.items():
        if v >= peak_val:
            if in_dd:
                rows.append({"peak_year": peak_year, "trough_year": trough_year, "depth": trough_val / peak_val - 1,
                             "recovery_year": y, "years_to_trough": trough_year - peak_year, "years_to_recover": y - trough_year})
                in_dd = False
            peak_year, peak_val = y, v
        else:
            if not in_dd or v < trough_val:
                trough_year, trough_val = y, v
            in_dd = True
    if in_dd:
        rows.append({"peak_year": peak_year, "trough_year": trough_year, "depth": trough_val / peak_val - 1,
                     "recovery_year": None, "years_to_trough": trough_year - peak_year, "years_to_recover": None})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["depth"] <= -0.03].reset_index(drop=True)   # ignore wiggles under 3%
    labels = []
    for r in df.itertuples():
        # An episode labels a drawdown when it overlaps the decline (peak+1 to trough).
        hit = [name for (y0, y1), name in EPISODES if y0 <= r.trough_year and y1 >= r.peak_year + 1 and y0 >= s.index[0]]
        labels.append("; ".join(hit))
    df["episode"] = labels
    df["measure"] = measure
    return df


def decomposition(a: pd.DataFrame) -> pd.DataFrame:
    d = a[["year", "occ_growth", "demand_growth", "supply_growth", "covid"]].dropna().copy()
    d["dlog_occ"] = np.log1p(d["occ_growth"])
    d["from_demand"] = np.log1p(d["demand_growth"])
    d["from_supply"] = -np.log1p(d["supply_growth"])
    return d[["year", "occ_growth", "dlog_occ", "from_demand", "from_supply", "covid"]]


def seasonality(m: pd.DataFrame, cfg: MarketConfig, recent_years: int = 8) -> tuple[pd.DataFrame, dict]:
    covid = set(cfg.estimation.covid_years)
    x = m.copy()
    x["year"] = x["period_start"].dt.year
    x["month"] = x["period_start"].dt.month
    full = x.groupby("year")["month"].count()
    years = [y for y in full.index if full[y] == 12 and y not in covid]
    x = x[x["year"].isin(years)]
    tot = x.groupby("year")[["demand", "revenue"]].transform("sum")
    x["demand_share"] = x["demand"] / tot["demand"]
    x["adr_index"] = x["adr"] / (tot["revenue"] / tot["demand"])
    recent = years[-recent_years:]
    early = [y for y in years if y not in recent]
    out = pd.DataFrame({"month": range(1, 13)})
    for label, ys in (("early", early), ("recent", recent)):
        sub = x[x["year"].isin(ys)].groupby("month")[["demand_share", "adr_index"]].mean()
        out[f"demand_share_{label}"] = out["month"].map(sub["demand_share"])
        out[f"adr_index_{label}"] = out["month"].map(sub["adr_index"])
    shift = {
        "early_years": f"{early[0]}-{early[-1]}" if early else "",
        "recent_years": f"{recent[0]}-{recent[-1]}" if recent else "",
        "max_demand_share_change_pts": float((out["demand_share_recent"] - out["demand_share_early"]).abs().max()) if early else None,
        "max_adr_index_change": float((out["adr_index_recent"] - out["adr_index_early"]).abs().max()) if early else None,
    }
    return out, shift


def occupancy_ceiling(m: pd.DataFrame, cfg: MarketConfig) -> dict:
    """Annual ceiling = occupancy if every month ran at its historical peak,
    weighted by the latest full year's monthly supply. Pre-COVID and post-COVID
    complete years count; COVID years do not."""
    covid = set(cfg.estimation.covid_years)
    x = m.copy()
    x["year"] = x["period_start"].dt.year
    x["month"] = x["period_start"].dt.month
    full = x.groupby("year")["month"].count()
    ok = [y for y in full.index if full[y] == 12 and y not in covid]
    x = x[x["year"].isin(ok)]
    peak = x.groupby("month")["occupancy"].max()
    last = x[x["year"] == max(ok)].set_index("month")["supply"]
    ceiling = float((peak * last).sum() / last.sum())
    return {"annual": ceiling, "monthly_peak": {int(k): float(v) for k, v in peak.items()},
            "monthly_max": float(peak.max()),
            "basis": f"Each month at its highest occupancy in {min(ok)} to {max(ok)} (COVID years excluded), "
                     f"weighted by {max(ok)} monthly supply."}


def analyse_history(monthly_spliced: pd.DataFrame, sample: pd.DataFrame, cfg: MarketConfig,
                    cpi: dict[int, float] | None = None) -> HistoryResult:
    a = build_annual(monthly_spliced, sample, cpi)
    seas, shift = seasonality(monthly_spliced, cfg)
    cyc_r = cycle_map(a, "revpar")
    cyc_d = cycle_map(a, "demand")
    notes = []
    if not cpi:
        notes.append("Real ADR is not reported: the CPI history is not loaded (gap).")
    if monthly_spliced.get("spliced", pd.Series(False)).any():
        notes.append("Levels before a confirmed series break are rescaled (spliced); see the validation report.")
    pre = a[~a["covid"]]
    return HistoryResult(
        annual=a, growth_pcts=growth_percentiles(a), cycles=pd.concat([cyc_r, cyc_d], ignore_index=True),
        decomposition=decomposition(a), seasonality=seas, seasonality_shift=shift,
        ceiling=occupancy_ceiling(monthly_spliced, cfg),
        occ_annual_max=float(pre["occupancy"].max()), occ_annual_min=float(pre["occupancy"].min()),
        real_adr=bool(cpi), notes=notes)
