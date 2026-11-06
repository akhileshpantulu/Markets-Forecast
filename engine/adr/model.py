"""ADR model.

CPI mode:      dlog ADR(t) = pi(t) + alpha + pricing(t) + r(t)
Nominal mode:  dlog ADR(t) =         alpha + pricing(t) + r(t)
r(t) = rho * r(t-1) + u(t), u correlated with the demand innovation.

Pricing rule (user decision 2026-11-06, "both"):
  pricing(t) = beta        * occupancy change from a downturn or its recovery   (rates fall with a shock)
             + beta_change * occupancy change in normal years                   (inverse: <= 0 by default)
             + beta_level  * (last year's occupancy - the market's normal level)  (dampening, <= 0)
             + beta_high   * max(last year's occupancy - high_threshold, 0)     (extra dampening at 90%+)
beta is fitted on all usable years, constrained non-negative and shrunk toward its prior. beta_change is a
labelled assumption: history shows occupancy and rate moving together in normal years (beta_change_hat).
beta_level is fitted on normal years and shrunk toward a non-positive prior; beta_high is an assumption.
Nominal mode is the fallback when no CPI history is loaded; alpha then carries the average nominal drift.
numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.demand.baseline import DemandModel, _shrink


@dataclass
class AdrModel:
    mode: str                  # cpi | nominal
    alpha: float
    beta: float
    beta_hat: float
    se_beta: float
    w_beta: float
    rho: float
    rho_hat: float
    w_rho: float
    sigma_u: float
    sigma_u_hat: float
    corr: float
    corr_hat: float
    w_corr: float
    years: list[int]
    last_r: float
    last_year: int
    notes: list[str] = field(default_factory=list)
    occ_trend: float = 0.0    # anchored mode: the change response acts on occupancy change beyond its long-run trend
    beta_change: float = 0.0          # ADR response to occupancy change in normal years (assumption)
    beta_change_hat: float = float("nan")
    beta_level: float = 0.0           # per unit of last year's occupancy above occ_ref
    beta_level_hat: float = float("nan")
    se_beta_level: float = float("nan")
    w_beta_level: float = 0.0
    beta_high: float = 0.0            # extra, per unit above high_threshold
    high_threshold: float = 0.90
    occ_ref: float = 0.0              # the market's normal annual occupancy

    def level_effect(self, occ_prev):
        """Rate dampening (log points) from last year's occupancy level."""
        return self.beta_level * (occ_prev - self.occ_ref) + self.beta_high * np.maximum(occ_prev - self.high_threshold, 0.0)

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("mode", "alpha", "beta", "beta_hat", "se_beta", "w_beta", "rho", "rho_hat",
                                               "w_rho", "sigma_u", "sigma_u_hat", "corr", "corr_hat", "w_corr", "years",
                                               "last_r", "last_year", "notes", "occ_trend", "beta_change", "beta_change_hat",
                                               "beta_level", "beta_level_hat", "se_beta_level", "w_beta_level", "beta_high",
                                               "high_threshold", "occ_ref")}


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Slope and its standard error for y on x with an intercept."""
    if len(x) < 4 or np.ptp(x) == 0:
        return float("nan"), float("nan")
    xc = x - x.mean()
    b = float((xc * (y - y.mean())).sum() / (xc ** 2).sum())
    res = y - y.mean() - b * xc
    return b, float(math.sqrt((res ** 2).sum() / (len(x) - 2) / (xc ** 2).sum()))


def fit_adr(annual: pd.DataFrame, demand: DemandModel, sample: pd.DataFrame, priors: dict,
            cpi: dict[int, float] | None = None) -> AdrModel:
    """annual: HistoryResult.annual (complete years, growth columns, use flags)."""
    pa = priors["adr"]
    a = annual.dropna(subset=["adr_growth", "occ_growth"])
    a = a[a["use_adr"] & a["use_occ"]]
    mode = "cpi" if cpi else "nominal"
    notes = []
    if mode == "cpi":
        a = a[a["year"].isin(cpi)]
        y = np.log1p(a["adr_growth"].to_numpy()) - np.log1p(a["year"].map(cpi).to_numpy())
    else:
        y = np.log1p(a["adr_growth"].to_numpy())
        notes.append("Nominal mode: CPI history not loaded, so alpha is the average nominal ADR drift.")
    x = np.log1p(a["occ_growth"].to_numpy())
    years = [int(v) for v in a["year"]]
    n = len(y)
    xc = x - x.mean()
    beta_hat = float((xc * (y - y.mean())).sum() / (xc ** 2).sum()) if n > 2 else float("nan")
    res0 = y - y.mean() - beta_hat * xc
    se_beta = math.sqrt((res0 ** 2).sum() / max(n - 2, 1) / (xc ** 2).sum()) if n > 2 else float("nan")
    beta, w_beta = _shrink(beta_hat, se_beta, pa["beta_pricing_power"]["mean"], pa["beta_pricing_power"]["sd"])
    beta = max(beta, pa["beta_pricing_power"]["lower"])

    # Occupancy level: last year's annual occupancy against the market's normal level.
    occ_lvl = annual.set_index("year")["occupancy"] if "occupancy" in annual else pd.Series(dtype=float)
    normal_lvl = annual.loc[annual["use_occ"] & ~annual.get("covid", pd.Series(False, index=annual.index)), "occupancy"] \
        if "occupancy" in annual else pd.Series(dtype=float)
    occ_ref = float(normal_lvl.mean()) if len(normal_lvl) else 0.0
    occ_prev = np.array([occ_lvl.get(t - 1, np.nan) for t in years], float)
    occ_prev = np.where(np.isnan(occ_prev), occ_ref, occ_prev)
    shockish = set(demand.shock_years) | set(demand.recovery_years)
    calm = np.array([t not in shockish for t in years], bool)

    pc_ = pa.get("occ_change_response", {"value": 0.0})
    beta_change = float(pc_["value"])
    beta_change_hat, _ = _ols(x[calm], y[calm])
    ph = pa.get("high_occupancy", {"threshold": 0.90, "dampening": 0.0})
    thr, beta_high = float(ph["threshold"]), float(ph["dampening"])
    hi = np.maximum(occ_prev - thr, 0.0)
    pl = pa.get("occ_level_dampening", {"mean": 0.0, "sd": 1e-6, "upper": 0.0})
    z = y - beta_change * x - beta_high * hi
    bl_hat, se_bl = _ols(occ_prev[calm], z[calm])
    beta_level, w_bl = _shrink(bl_hat, se_bl, pl["mean"], pl["sd"])
    beta_level = min(beta_level, pl.get("upper", 0.0))

    pricing = np.where(calm, beta_change * x + beta_level * (occ_prev - occ_ref) + beta_high * hi, beta * x)
    alpha = float((y - pricing).mean())
    r = pd.Series(y - alpha - pricing, index=years)

    pairs = [(r[t - 1], r[t]) for t in years if (t - 1) in r.index]
    if len(pairs) >= 3:
        xr = np.array([p[0] for p in pairs]); yr = np.array([p[1] for p in pairs])
        rho_hat = float((xr * yr).sum() / (xr * xr).sum())
        se_rho = math.sqrt(((yr - rho_hat * xr) ** 2).sum() / max(len(pairs) - 1, 1) / (xr * xr).sum())
    else:
        rho_hat, se_rho = float("nan"), float("nan")
    rho, w_rho = _shrink(rho_hat, se_rho, pa["residual_phi"]["mean"], pa["residual_phi"]["sd"])
    rho = float(np.clip(rho, pa["residual_phi"]["lower"], pa["residual_phi"]["upper"]))

    u = pd.Series({t: r[t] - rho * r[t - 1] for t in years if (t - 1) in r.index})
    if len(u) < 3:
        u = r.copy()
    sigma_u_hat = float(np.sqrt((u ** 2).sum() / max(len(u) - 1, 1)))
    n0 = pa["residual_sd"]["pseudo_obs"]
    w_s = len(u) / (len(u) + n0)
    sigma_u = w_s * sigma_u_hat + (1 - w_s) * pa["residual_sd"]["mean"]

    # Correlation with demand innovations in years where both exist.
    pc = priors["correlation"]["demand_adr_residual"]
    e = {}
    for yr in demand.normal_years:
        if (yr - 1) in demand.normal_years:
            e[yr] = None
    g = sample.set_index("year")["g_demand"]
    for yr in list(e):
        e[yr] = g[yr] - demand.mu - demand.phi * (g[yr - 1] - demand.mu)
    common = [t for t in u.index if t in e]
    if len(common) >= 5:
        corr_hat = float(np.corrcoef([u[t] for t in common], [e[t] for t in common])[0, 1])
        se_c = (1 - corr_hat ** 2) / math.sqrt(len(common) - 1)
    else:
        corr_hat, se_c = float("nan"), float("nan")
        notes.append("Too few overlapping years to estimate the demand and ADR residual correlation; prior used.")
    corr, w_corr = _shrink(corr_hat, se_c, pc["mean"], pc["sd"])
    corr = float(np.clip(corr, -0.9, 0.9))
    last_year = max(years) if years else 0
    if not np.isnan(beta_change_hat) and beta_change_hat > 0 > beta_change:
        notes.append(f"Occupancy and ADR moved together in normal years (slope {beta_change_hat:.2f}); the inverse response "
                     f"of {beta_change:.2f} is a user decision, so the ADR residual absorbs the difference.")
    return AdrModel(mode, alpha, float(beta), beta_hat, se_beta, w_beta, rho, rho_hat, w_rho, float(sigma_u),
                    sigma_u_hat, corr, corr_hat, w_corr, years, float(r.iloc[-1]) if len(r) else 0.0, last_year, notes,
                    beta_change=beta_change, beta_change_hat=beta_change_hat, beta_level=float(beta_level),
                    beta_level_hat=bl_hat, se_beta_level=se_bl, w_beta_level=float(w_bl), beta_high=beta_high,
                    high_threshold=thr, occ_ref=occ_ref)
