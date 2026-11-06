"""Baseline simulation: stub year (monthly) plus full years (annual).

Stub year Y0: actual YTD months; remaining months = prior-year same months x
exp(weighted YTD YoY trend) x exp(error), where the error is bootstrapped from
how that same rule performed in past years at the same YTD cutoff (so the stub
band is calibrated and narrows as more months are observed).

Y1..Y5: organic demand follows the AR(1) + shock model; induced demand from new
and reopened rooms is added; occupancy = demand / supply capped at the ceiling;
ADR follows the ADR model with innovations correlated to demand. numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.adr.model import AdrModel
from engine.demand.baseline import DemandModel, draw_innovations, simulate_shocks
from engine.supply.pipeline import SupplyPaths

STAT_PCTS = (10, 25, 50, 75, 90)
HALF_LIFE_MONTHS = 3.0
ADR_X_CLIP = 1.5      # occupancy change entering the ADR equation is clipped to 1.5x the fitted range


@dataclass
class StubCalibration:
    cutoff_month: int
    years: list[int]
    err_demand: np.ndarray
    err_adr: np.ndarray
    trend_demand: float
    trend_adr: float


@dataclass
class Paths:
    years: list[int]                  # Y0..Yn
    base_year: int                    # last complete actual year before Y0
    occ: np.ndarray                   # [paths, years]
    adr: np.ndarray
    demand: np.ndarray
    supply: np.ndarray
    base: dict                        # actual base-year levels
    stub_monthly: dict | None = None  # actual and simulated months of Y0
    shock_onset: np.ndarray | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def revpar(self) -> np.ndarray:
        return self.occ * self.adr

    def prev(self, arr: np.ndarray, base_value: float) -> np.ndarray:
        return np.concatenate([np.full((arr.shape[0], 1), base_value), arr[:, :-1]], axis=1)

    def growth(self) -> dict[str, np.ndarray]:
        b = self.base
        po = self.prev(self.occ, b["occupancy"])
        pa = self.prev(self.adr, b["adr"])
        pr = self.prev(self.revpar, b["revpar"])
        pdm = self.prev(self.demand, b["demand"])
        ps = self.prev(self.supply, b["supply"])
        return {"occ_growth": self.occ / po - 1, "occ_pts": self.occ - po, "adr_growth": self.adr / pa - 1,
                "revpar_growth": self.revpar / pr - 1, "demand_growth": self.demand / pdm - 1,
                "supply_growth": self.supply / ps - 1}


def _weights(n: int) -> np.ndarray:
    w = 0.5 ** ((n - 1 - np.arange(n)) / HALF_LIFE_MONTHS)
    return w / w.sum()


def _ytd_trend(monthly: pd.DataFrame, year: int, cutoff: int, col: str) -> float:
    m = monthly.set_index("period_start")
    cur = [m.loc[pd.Timestamp(year=year, month=k, day=1), col] for k in range(1, cutoff + 1)]
    prv = [m.loc[pd.Timestamp(year=year - 1, month=k, day=1), col] for k in range(1, cutoff + 1)]
    ly = np.log(np.asarray(cur, float) / np.asarray(prv, float))
    return float((ly * _weights(cutoff)).sum())


def _rest(monthly: pd.DataFrame, year: int, cutoff: int) -> tuple[float, float]:
    m = monthly[(monthly["period_start"].dt.year == year) & (monthly["period_start"].dt.month > cutoff)]
    d = float(m["demand"].sum())
    return d, float(m["revenue"].sum()) / d


def calibrate_stub(monthly: pd.DataFrame, cutoff: int, covid_years: list[int], last_year: int) -> StubCalibration:
    """Errors of the stub rule in past years at the same cutoff."""
    covid = set(covid_years)
    m = monthly.copy()
    counts = m.groupby(m["period_start"].dt.year)["period_start"].count()
    full = {int(y) for y, c in counts.items() if c == 12}
    ed, ea, ys = [], [], []
    for y in sorted(full):
        if y >= last_year or (y - 1) not in full or y in covid or (y - 1) in covid:
            continue
        td = _ytd_trend(m, y, cutoff, "demand")
        ta = _ytd_trend(m, y, cutoff, "adr")
        dp, ap = _rest(m, y - 1, cutoff)
        da, aa = _rest(m, y, cutoff)
        ed.append(math.log(da / (dp * math.exp(td))))
        ea.append(math.log(aa / (ap * math.exp(ta))))
        ys.append(y)
    td = _ytd_trend(m, last_year, cutoff, "demand")
    ta = _ytd_trend(m, last_year, cutoff, "adr")
    return StubCalibration(cutoff, ys, np.array(ed), np.array(ea), td, ta)


def project_years(dm: DemandModel, am: AdrModel, rng: np.random.Generator, n_paths: int, n_years: int,
                  d0: np.ndarray, d_prev: np.ndarray, induced0: np.ndarray, s0: np.ndarray, occ0: np.ndarray,
                  occ_prev: np.ndarray, adr0: np.ndarray, adr_prev: np.ndarray, supply: np.ndarray, induced: np.ndarray,
                  ceiling: float, cpi: np.ndarray | None = None, spread: float = 1.0, x_range: tuple[float, float] = (-0.15, 0.15),
                  cpi0: np.ndarray | float = 0.0, trace: dict | None = None, levels: dict | None = None):
    """Annual projection from an origin year (index 0 = origin, already known per path).

    supply, induced: [paths, n_years] annual room nights for years 1..n.
    cpi: [paths, n_years] inflation (decimal) in CPI mode; cpi0: origin-year inflation.
    spread: float or [n_years] multiplier on the demand and ADR innovations.
    levels: driver level effects (log) {"demand": [paths, n_years+1], "adr": [paths, n_years+1],
            "shock_mult": [paths, n_years]}; column 0 is the origin year.
    Returns occ, adr, demand arrays [paths, n_years] and shock onset codes.
    """
    lv = levels or {}
    Ld = lv.get("demand", np.zeros((n_paths, n_years + 1)))
    La = lv.get("adr", np.zeros((n_paths, n_years + 1)))
    spread_t = np.broadcast_to(np.asarray(spread, float), (n_years,))
    z = draw_innovations(dm, rng, (n_paths, n_years))
    z2 = rng.standard_normal((n_paths, n_years))
    dev, onset = simulate_shocks(dm, rng, n_paths, n_years, lv.get("shock_mult"))
    c = am.corr
    g_lag = np.log(d0 / d_prev)
    org = (d0 - induced0) * np.exp(-Ld[:, 0])           # organic demand excludes driver effects
    dlog_occ0 = np.log(occ0 / occ_prev)
    pi0 = np.log1p(cpi0) if cpi is not None else 0.0
    r = np.log(adr0 / adr_prev) - pi0 - am.alpha - am.beta * np.clip(dlog_occ0 - am.occ_trend, *x_range) - La[:, 0]
    occ_out = np.empty((n_paths, n_years)); adr_out = np.empty((n_paths, n_years)); dem_out = np.empty((n_paths, n_years))
    occ_p, adr_p, gn = occ0, adr0, g_lag
    # Ratios at the origin for the trace: total/organic demand, and capped/uncapped demand.
    ind_ratio_p = np.log(d0 / np.maximum(org * np.exp(Ld[:, 0]), 1e-9))
    cap_ratio_p = np.zeros(n_paths)
    dem_p, s_p = d0, s0
    for t in range(n_years):
        sig = dm.sigma * spread_t[t]
        sig_u = am.sigma_u * spread_t[t]
        momentum = dm.phi * (gn - dm.mu)
        noise = sig * z[:, t]
        gn = dm.mu + momentum + noise
        org = org * np.exp(gn + dev[:, t])
        with_drivers = org * np.exp(Ld[:, t + 1])
        unc = with_drivers + induced[:, t]
        occ = np.minimum(unc / supply[:, t], ceiling)
        dem = occ * supply[:, t]
        carry = am.rho * r
        r = carry + sig_u * (c * z[:, t] + math.sqrt(1 - c * c) * z2[:, t])
        x = np.clip(np.log(occ / occ_p) - am.occ_trend, *x_range)
        pi = np.log1p(cpi[:, t]) if cpi is not None else 0.0
        d_la = La[:, t + 1] - La[:, t]
        adr = adr_p * np.exp(pi + am.alpha + am.beta * x + r + d_la)
        if trace is not None:
            ind_ratio = np.log(unc / with_drivers)
            cap_ratio = np.log(dem / unc)
            rec = {
                "demand_trend": dm.mu, "demand_momentum": momentum.mean(), "demand_noise": noise.mean(),
                "demand_shocks": dev[:, t].mean(), "demand_drivers": (Ld[:, t + 1] - Ld[:, t]).mean(),
                "demand_new_hotels": (ind_ratio - ind_ratio_p).mean(),
                "demand_ceiling": (cap_ratio - cap_ratio_p).mean(), "demand_total": np.log(dem / dem_p).mean(),
                "supply_total": np.log(supply[:, t] / s_p).mean(),
                "supply_drivers": (lv.get("supply", np.zeros((n_paths, n_years + 1)))[:, t + 1]
                                   - lv.get("supply", np.zeros((n_paths, n_years + 1)))[:, t]).mean(),
                "adr_inflation": float(np.mean(pi)), "adr_real_drift": am.alpha, "adr_pricing_power": (am.beta * x).mean(),
                "adr_carry_over": carry.mean(), "adr_noise": (r - carry).mean(), "adr_drivers": d_la.mean(),
                "adr_total": np.log(adr / adr_p).mean(),
                "share_at_ceiling": float((occ >= ceiling - 1e-12).mean()),
            }
            calm = dev[:, t] == 0
            if calm.any():
                g_d = np.log(dem / dem_p)[calm]
                g_s = np.log(supply[:, t] / s_p)[calm]
                g_a = np.log(adr / adr_p)[calm]
                rec.update({"calm_share": calm.mean(), "calm_demand_total": g_d.mean(), "calm_supply_total": g_s.mean(),
                            "calm_occ_total": (g_d - g_s).mean(), "calm_adr_total": g_a.mean(),
                            "calm_demand_momentum": momentum[calm].mean(),
                            "calm_demand_drivers": (Ld[:, t + 1] - Ld[:, t])[calm].mean(),
                            "calm_adr_drivers": d_la[calm].mean(),
                            "calm_demand_new_hotels": (ind_ratio - ind_ratio_p)[calm].mean(),
                            "calm_demand_ceiling": (cap_ratio - cap_ratio_p)[calm].mean(),
                            "calm_adr_pricing_power": (am.beta * x)[calm].mean()})
            trace.setdefault("years", []).append({k: float(v) for k, v in rec.items()})
            ind_ratio_p, cap_ratio_p = ind_ratio, cap_ratio
        dem_p, s_p = dem, supply[:, t]
        occ_out[:, t], adr_out[:, t], dem_out[:, t] = occ, adr, dem
        occ_p, adr_p = occ, adr
    return occ_out, adr_out, dem_out, onset


def simulate_baseline(monthly: pd.DataFrame, annual_hist: pd.DataFrame, dm: DemandModel, am: AdrModel,
                      sp: SupplyPaths, ceiling: dict, horizon: int, n_paths: int, rng: np.random.Generator,
                      covid_years: list[int], spread: float = 1.0, cpi_paths: np.ndarray | None = None,
                      cpi0: np.ndarray | float = 0.0, trace: dict | None = None, levels: dict | None = None) -> Paths:
    """levels: driver level effects (log) by year Y0..Yn: "demand", "adr", "supply" [paths, horizon+1] and
    "shock_mult" [paths, horizon] for Y1..Yn. In the stub year they apply to the simulated months only."""
    zeros = np.zeros((n_paths, horizon + 1))
    lv = levels or {}
    Ld, La, Ls = lv.get("demand", zeros), lv.get("adr", zeros), lv.get("supply", zeros)
    m = monthly.sort_values("period_start").reset_index(drop=True)
    last = m["period_start"].iloc[-1]
    y0 = int(last.year)
    cutoff = int(last.month)
    notes: list[str] = []
    cur = m[m["period_start"].dt.year == y0]
    prv = m[m["period_start"].dt.year == y0 - 1]
    base = {"year": y0 - 1, "supply": float(prv["supply"].sum()), "demand": float(prv["demand"].sum()),
            "revenue": float(prv["revenue"].sum())}
    base["occupancy"] = base["demand"] / base["supply"]
    base["adr"] = base["revenue"] / base["demand"]
    base["revpar"] = base["revenue"] / base["supply"]
    ytd_s, ytd_d, ytd_r = float(cur["supply"].sum()), float(cur["demand"].sum()), float(cur["revenue"].sum())

    month_years = np.array([d.year for d in sp.months])
    sup_m = sp.supply
    ind_m = sp.induced_demand()
    stub_monthly = None
    if cutoff < 12:
        cal = calibrate_stub(m, cutoff, covid_years, y0)
        if len(cal.err_demand) < 5:
            notes.append(f"Only {len(cal.err_demand)} past years calibrate the stub; its band is less reliable.")
        k = rng.integers(0, max(len(cal.err_demand), 1), n_paths)
        ed = cal.err_demand[k] if len(cal.err_demand) else np.zeros(n_paths)
        ea = cal.err_adr[k] if len(cal.err_adr) else np.zeros(n_paths)
        rest_prev = prv[prv["period_start"].dt.month > cutoff].sort_values("period_start")
        n_rest = len(rest_prev)
        stub_idx = np.nonzero(month_years == y0)[0]
        assert len(stub_idx) == n_rest, "stub months must match remaining months"
        d_prev_m = rest_prev["demand"].to_numpy()
        a_prev_m = (rest_prev["revenue"] / rest_prev["demand"]).to_numpy()
        peak = np.array([ceiling["monthly_peak"].get(int(p.month), 0.97) for p in rest_prev["period_start"]])
        cap_m = sup_m[:, stub_idx] * peak[None, :]
        base_m = d_prev_m[None, :] * np.exp(cal.trend_demand + ed)[:, None]
        d_nodrv = np.minimum(base_m + ind_m[:, stub_idx], cap_m)
        d_m = np.minimum(base_m * np.exp(Ld[:, 0])[:, None] + ind_m[:, stub_idx], cap_m)
        a_nodrv = a_prev_m[None, :] * np.exp(cal.trend_adr + ea)[:, None]
        a_m = a_nodrv * np.exp(La[:, 0])[:, None]
        s_rest = sup_m[:, stub_idx].sum(axis=1)
        d_rest = d_m.sum(axis=1)
        r_rest = (d_m * a_m).sum(axis=1)
        s_y0 = ytd_s + s_rest
        d_y0 = ytd_d + d_rest
        rev_y0 = ytd_r + r_rest
        d_y0_nodrv = ytd_d + d_nodrv.sum(axis=1)
        rev_y0_nodrv = ytd_r + (d_nodrv * a_nodrv).sum(axis=1)
        ld0 = np.log(d_y0 / d_y0_nodrv)
        la0 = np.log((rev_y0 / d_y0) / (rev_y0_nodrv / d_y0_nodrv))
        ind_y0 = ind_m[:, stub_idx].sum(axis=1)
        stub_monthly = {
            "actual": [{"period": p.strftime("%Y-%m"), "occupancy": float(o), "adr": float(a)}
                       for p, o, a in zip(cur["period_start"], cur["occupancy"], cur["adr"])],
            "simulated": [{"period": sp.months[i].strftime("%Y-%m"),
                           **{f"occ_p{q}": float(np.percentile(d_m[:, j] / sup_m[:, i], q)) for q in STAT_PCTS},
                           **{f"adr_p{q}": float(np.percentile(a_m[:, j], q)) for q in STAT_PCTS}}
                          for j, i in enumerate(stub_idx)],
            "calibration_years": cal.years, "trend_demand": cal.trend_demand, "trend_adr": cal.trend_adr,
            "err_demand_sd": float(cal.err_demand.std(ddof=1)) if len(cal.err_demand) > 1 else None,
            "err_adr_sd": float(cal.err_adr.std(ddof=1)) if len(cal.err_adr) > 1 else None,
        }
    else:
        s_y0 = np.full(n_paths, ytd_s); d_y0 = np.full(n_paths, ytd_d); rev_y0 = np.full(n_paths, ytd_r)
        ind_y0 = np.zeros(n_paths)
        ld0 = la0 = np.zeros(n_paths)

    occ_y0 = d_y0 / s_y0
    adr_y0 = rev_y0 / d_y0
    years = [y0 + i for i in range(horizon + 1)]
    sup_y = np.stack([sup_m[:, month_years == y].sum(axis=1) for y in years[1:]], axis=1)
    ind_y = np.stack([ind_m[:, month_years == y].sum(axis=1) for y in years[1:]], axis=1)
    sup_y = sup_y * np.exp(Ls[:, 1:])                      # supply drivers (e.g. projects missing from the export)
    Ls_eff = Ls.copy(); Ls_eff[:, 0] = 0.0
    lv_pass = {"demand": np.column_stack([ld0, Ld[:, 1:]]), "adr": np.column_stack([la0, La[:, 1:]]),
               "supply": Ls_eff}
    if "shock_mult" in lv:
        lv_pass["shock_mult"] = lv["shock_mult"]
    occ, adr, dem, onset = project_years(
        dm, am, rng, n_paths, horizon, d_y0, np.full(n_paths, base["demand"]), ind_y0, s_y0, occ_y0,
        np.full(n_paths, base["occupancy"]), adr_y0, np.full(n_paths, base["adr"]), sup_y, ind_y,
        ceiling["annual"], cpi_paths, spread, _x_range(annual_hist), cpi0, trace, lv_pass)
    if trace is not None:
        trace["y0_supply"] = s_y0
        trace["sup_y"] = sup_y
        trace["y0_rest_idx"] = np.nonzero(month_years == y0)[0]
    return Paths(years, y0 - 1, np.column_stack([occ_y0, occ]), np.column_stack([adr_y0, adr]),
                 np.column_stack([d_y0, dem]), np.column_stack([s_y0, sup_y]), base, stub_monthly,
                 np.column_stack([np.zeros(n_paths, dtype=np.int8), onset]), notes)


def _x_range(annual_hist: pd.DataFrame) -> tuple[float, float]:
    x = np.log1p(annual_hist.loc[annual_hist["use_occ"], "occ_growth"].dropna().to_numpy())
    if len(x) == 0:
        return (-0.15, 0.15)
    return (float(x.min()) * ADR_X_CLIP, float(x.max()) * ADR_X_CLIP)


def summarise(paths: Paths) -> list[dict]:
    """Per-year distribution summary."""
    g = paths.growth()
    levels = {"occupancy": paths.occ, "adr": paths.adr, "revpar": paths.revpar}
    out = []
    for j, y in enumerate(paths.years):
        row = {"year": y, "label": "Y0 (stub)" if j == 0 else f"Y{j}"}
        for name, arr in {**g, **levels}.items():
            v = arr[:, j]
            row[name] = {"mean": float(v.mean()), **{f"p{q}": float(np.percentile(v, q)) for q in STAT_PCTS}}
        row["p_revpar_negative"] = float((g["revpar_growth"][:, j] < 0).mean())
        row["p_shock_onset"] = float((paths.shock_onset[:, j] > 0).mean()) if paths.shock_onset is not None else None
        out.append(row)
    return out


def position_vs_history(summary: list[dict], growth_pcts: pd.DataFrame, annual_hist: pd.DataFrame,
                        occ_max: float) -> list[dict]:
    """Percentile of each P50 in the market's ex-COVID history, nearest analog year, range flags."""
    ex = growth_pcts[growth_pcts["sample"] == "ex_covid"].set_index("measure")
    hist = annual_hist[~annual_hist["covid"] & annual_hist["use_adr"] & annual_hist["use_occ"]].dropna(subset=["occ_growth", "adr_growth"])
    out = []
    sd_o = hist["occ_growth"].std(ddof=1) or 1.0
    sd_a = hist["adr_growth"].std(ddof=1) or 1.0
    for row in summary:
        pos = {"year": row["year"]}
        for meas in ("occ_growth", "adr_growth", "revpar_growth"):
            p50 = row[meas]["p50"]
            col = {"occ_growth": "occ_growth", "adr_growth": "adr_growth", "revpar_growth": "revpar_growth"}[meas]
            v = hist[col].dropna().to_numpy()
            pct = float((v <= p50).mean()) if len(v) else None
            lo, hi = (ex.loc[meas, "p5"], ex.loc[meas, "p95"]) if meas in ex.index else (None, None)
            pos[meas] = {"percentile": pct, "outside_p5_p95": bool(lo is not None and (p50 < lo or p50 > hi))}
        d = ((hist["occ_growth"] - row["occ_growth"]["p50"]) / sd_o) ** 2 + ((hist["adr_growth"] - row["adr_growth"]["p50"]) / sd_a) ** 2
        pos["analog_year"] = int(hist.loc[d.idxmin(), "year"]) if len(d) else None
        pos["occ_above_hist_max"] = bool(row["occupancy"]["p50"] > occ_max)
        out.append(pos)
    return out
