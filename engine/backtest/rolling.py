"""Rolling-origin backtest of the baseline engine.

For each origin year T with enough history, the models are refitted on data up
to T only, and years T+1..T+5 are simulated and compared with what happened.

Conditioning, stated plainly:
  - Supply is the realised supply (no historical pipeline snapshots exist), so
    occupancy and RevPAR are tested conditional on supply.
  - Series breaks are known from the full data (they are data artefacts).
  - Drivers cannot be backtested point in time; this tests the baseline only.
numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.adr.model import fit_adr
from engine.config import MarketConfig
from engine.demand.baseline import fit_demand
from engine.history.analysis import occupancy_ceiling
from engine.simulate.baseline import _x_range, project_years

MEASURES = ("demand_growth", "occ_growth", "adr_growth", "revpar_growth")
SPREAD_GRID = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0)


def crps_sample(x: np.ndarray, y: float) -> float:
    x = np.sort(np.asarray(x, float))
    n = len(x)
    e1 = np.abs(x - y).mean()
    i = np.arange(1, n + 1)
    e2 = (2.0 / (n * n)) * ((2 * i - n - 1) * x).sum()
    return float(e1 - 0.5 * e2)


@dataclass
class Variant:
    include_covid: bool
    residual_type: str

    @property
    def key(self) -> str:
        return f"{'with' if self.include_covid else 'ex'}_covid/{self.residual_type}"


@dataclass
class BacktestResult:
    records: pd.DataFrame                  # one row per origin x horizon x measure x variant x spread
    origins: list[int]
    chosen: str
    spread: float
    calibrated: bool
    coverage: pd.DataFrame                 # chosen variant, spread 1.0 and chosen spread, by horizon x measure
    benchmarks: pd.DataFrame               # by measure x horizon: model CRPS, model MAE, zero MAE, mean MAE
    variants: pd.DataFrame                 # variant comparison
    notes: list[str] = field(default_factory=list)


def _annual_levels(monthly: pd.DataFrame) -> pd.DataFrame:
    g = monthly.groupby(monthly["period_start"].dt.year)
    a = pd.DataFrame({"supply": g["supply"].sum(), "demand": g["demand"].sum(), "revenue": g["revenue"].sum(),
                      "months": g["period_start"].count()})
    a = a[a["months"] == 12]
    a["occupancy"] = a["demand"] / a["supply"]
    a["adr"] = a["revenue"] / a["demand"]
    a["revpar"] = a["revenue"] / a["supply"]
    return a


NAIVE_CPI_TARGET = 0.02
NAIVE_CPI_HALF_LIFE = 1.0
NAIVE_CPI_SD = 0.01


def naive_cpi_path(cpi: dict[int, float], origin: int, H: int, n: int, rng: np.random.Generator) -> np.ndarray:
    """Inflation forecast known at the origin: the last observed rate decaying to 2% (half-life one year),
    plus noise. Avoids giving the backtest knowledge of future inflation."""
    last = cpi.get(origin, NAIVE_CPI_TARGET)
    decay = 0.5 ** (np.arange(1, H + 1) / NAIVE_CPI_HALF_LIFE)
    mean = NAIVE_CPI_TARGET + (last - NAIVE_CPI_TARGET) * decay
    return mean[None, :] + rng.normal(0, NAIVE_CPI_SD, (n, H))


def run_backtest(monthly_spliced: pd.DataFrame, sample: pd.DataFrame, annual_hist: pd.DataFrame, cfg: MarketConfig,
                 priors: dict, seed: int, n_paths: int | None = None, max_h: int = 5,
                 cpi: dict[int, float] | None = None) -> BacktestResult:
    pb = priors["backtest"]
    n_paths = n_paths or pb["paths"]
    lv = _annual_levels(monthly_spliced)
    last = int(lv.index.max())
    covid = set(cfg.estimation.covid_years)
    usable_years = sample.loc[sample["use_demand"], "year"].astype(int).tolist()
    origins = [t for t in lv.index if t < last and (t - 1) in lv.index
               and sum(1 for y in usable_years if y <= t) >= pb["first_origin_min_years"]]
    variants = [Variant(False, "bootstrap"), Variant(False, "student_t"), Variant(True, "bootstrap"), Variant(True, "student_t")]

    def run(vi: int, var: Variant, k: float) -> list[dict]:
        out = []
        for T in origins:
            s_T = sample[sample["year"] <= T]
            a_T = annual_hist[annual_hist["year"] <= T]
            try:
                dm = fit_demand(s_T, priors, cfg.estimation.covid_years, var.include_covid, var.residual_type)
                cpi_T = {y: v for y, v in cpi.items() if y <= T} if cpi else None
                am = fit_adr(a_T, dm, s_T, priors, cpi_T)
            except Exception:  # too little data at this origin for this variant
                continue
            ceil = occupancy_ceiling(monthly_spliced[monthly_spliced["period_start"].dt.year <= T], cfg)["annual"]
            H = min(max_h, last - T)
            fut = [T + h for h in range(1, H + 1)]
            if any(y not in lv.index for y in fut):
                continue
            sup = np.tile(lv.loc[fut, "supply"].to_numpy(), (n_paths, 1))
            ind = np.zeros_like(sup)
            o, p = lv.loc[T], lv.loc[T - 1]
            hist_mean = {
                "demand_growth": float(np.expm1(s_T.loc[s_T["use_demand"], "g_demand"]).mean()),
                "occ_growth": float(np.expm1(s_T.loc[s_T["use_occupancy"], "g_occupancy"]).mean()),
                "adr_growth": float(np.expm1(s_T.loc[s_T["use_adr"], "g_adr"]).mean()),
                "revpar_growth": float(np.expm1(s_T.loc[s_T["use_revpar"], "g_revpar"]).mean()),
            }
            rng = np.random.default_rng([seed, vi, T, int(round(k * 100))])
            one = lambda v: np.full(n_paths, float(v))
            cp = naive_cpi_path(cpi, T, H, n_paths, rng) if (cpi and am.mode == "cpi") else None
            c0 = cpi.get(T, NAIVE_CPI_TARGET) if cp is not None else 0.0
            occ, adr, dem, _ = project_years(dm, am, rng, n_paths, H, one(o["demand"]), one(p["demand"]), one(0.0),
                                             one(o["supply"]), one(o["occupancy"]), one(p["occupancy"]),
                                             one(o["adr"]), one(p["adr"]), sup, ind, ceil, cp, k, _x_range(a_T), c0)
            prev_occ = np.column_stack([one(o["occupancy"]), occ[:, :-1]])
            prev_adr = np.column_stack([one(o["adr"]), adr[:, :-1]])
            prev_dem = np.column_stack([one(o["demand"]), dem[:, :-1]])
            sims = {"demand_growth": dem / prev_dem - 1, "occ_growth": occ / prev_occ - 1, "adr_growth": adr / prev_adr - 1,
                    "revpar_growth": (occ * adr) / (prev_occ * prev_adr) - 1}
            for h, y in enumerate(fut, start=1):
                act = {"demand_growth": lv.loc[y, "demand"] / lv.loc[y - 1, "demand"] - 1,
                       "occ_growth": lv.loc[y, "occupancy"] / lv.loc[y - 1, "occupancy"] - 1,
                       "adr_growth": lv.loc[y, "adr"] / lv.loc[y - 1, "adr"] - 1,
                       "revpar_growth": lv.loc[y, "revpar"] / lv.loc[y - 1, "revpar"] - 1}
                for meas in MEASURES:
                    x = sims[meas][:, h - 1]
                    q10, q25, q50, q75, q90 = np.percentile(x, [10, 25, 50, 75, 90])
                    yv = float(act[meas])
                    out.append({"variant": var.key, "spread": k, "origin": T, "h": h, "year": y, "measure": meas,
                                "actual": yv, "p10": q10, "p50": q50, "p90": q90,
                                "in80": q10 <= yv <= q90, "in50": q25 <= yv <= q75, "crps": crps_sample(x, yv),
                                "err_median": q50 - yv, "zero_abs": abs(yv), "mean_abs": abs(hist_mean[meas] - yv),
                                "covid_outcome": y in covid})
        return out

    rows = []
    for vi, var in enumerate(variants):
        rows += run(vi, var, 1.0)
    rec = pd.DataFrame(rows)
    notes = ["Supply is realised supply (conditioning): no historical pipeline snapshots exist.",
             "Drivers cannot be backtested point in time; this tests the baseline engine only.",
             ("ADR in CPI mode: inflation at each origin is forecast naively (last observed rate decaying to 2%, "
              "half-life one year, sd 1 point), so the backtest does not see future inflation.") if cpi else
             "ADR is in nominal mode in the backtest (CPI history not loaded)."]
    if rec.empty:
        return BacktestResult(rec, origins, "ex_covid/bootstrap", 1.0, False, pd.DataFrame(), pd.DataFrame(),
                              pd.DataFrame(), notes + ["Not enough history for a backtest."])

    base = rec[rec["spread"] == 1.0]
    vt = base.groupby("variant").agg(crps_all=("crps", "mean"), cov80_all=("in80", "mean"),
                                     crps_ex_covid=("crps", lambda s: s[~base.loc[s.index, "covid_outcome"]].mean()),
                                     cov80_ex_covid=("in80", lambda s: s[~base.loc[s.index, "covid_outcome"]].mean()),
                                     crps_demand=("crps", lambda s: s[base.loc[s.index, "measure"] == "demand_growth"].mean()))
    vt = vt.reset_index()
    # Residual type: by demand CRPS within the default COVID treatment. COVID treatment: by overall CRPS.
    ex = vt[vt["variant"].str.startswith("ex_covid")]
    best_resid = ex.sort_values("crps_demand").iloc[0]["variant"].split("/")[1]
    covid_choice = vt[vt["variant"].str.endswith(best_resid)].sort_values("crps_all").iloc[0]["variant"]
    chosen = covid_choice
    if chosen.startswith("with_covid"):
        notes.append("The backtest favours including COVID years in estimation; that variant is used.")

    lo, hi = pb["calibrate_if_outside"]
    k_used, calibrated = 1.0, False
    base_cov = rec[(rec["variant"] == chosen) & ~rec["covid_outcome"]]["in80"].mean()
    if not (lo <= base_cov <= hi):
        vi = [v.key for v in variants].index(chosen)
        extra = []
        for k in SPREAD_GRID:
            if k != 1.0:
                extra += run(vi, variants[vi], k)
        rec = pd.concat([rec, pd.DataFrame(extra)], ignore_index=True)
        ch = rec[(rec["variant"] == chosen) & ~rec["covid_outcome"]]
        cov_by_k = ch.groupby("spread")["in80"].mean()
        k_used = float((cov_by_k - pb["target_coverage"]).abs().idxmin())
        calibrated = k_used != 1.0
        notes.append(f"Coverage at spread 1.0 was {cov_by_k.get(1.0):.0%} (outside {lo:.0%} to {hi:.0%}); "
                     f"spreads are scaled by {k_used:g}, giving {cov_by_k[k_used]:.0%}.")

    def cov_table(k):
        sub = rec[(rec["variant"] == chosen) & (rec["spread"] == k)]
        t1 = sub.groupby(["measure", "h"]).agg(n=("in80", "size"), cov80=("in80", "mean"), cov50=("in50", "mean"),
                                                bias=("err_median", "mean"), crps=("crps", "mean"))
        sub2 = sub[~sub["covid_outcome"]]
        t2 = sub2.groupby(["measure", "h"]).agg(n_ex=("in80", "size"), cov80_ex=("in80", "mean"), cov50_ex=("in50", "mean"),
                                                 bias_ex=("err_median", "mean"), crps_ex=("crps", "mean"))
        t = t1.join(t2).reset_index()
        t["spread"] = k
        return t

    coverage = pd.concat([cov_table(1.0)] + ([cov_table(k_used)] if calibrated else []), ignore_index=True)
    sub = rec[(rec["variant"] == chosen) & (rec["spread"] == k_used)]
    bm = []
    for label, part in (("all", sub), ("ex_covid", sub[~sub["covid_outcome"]])):
        g = part.groupby(["measure", "h"]).agg(model_crps=("crps", "mean"), model_mae=("err_median", lambda s: s.abs().mean()),
                                               zero_mae=("zero_abs", "mean"), mean_mae=("mean_abs", "mean"), n=("crps", "size"))
        g["outcomes"] = label
        bm.append(g.reset_index())
    return BacktestResult(rec, origins, chosen, k_used, calibrated, coverage, pd.concat(bm, ignore_index=True), vt, notes)
