"""ADR model.

CPI mode (spec 6.5):  dlog ADR(t) = pi(t) + alpha + beta * dlog Occ(t) + r(t)
Nominal mode:         dlog ADR(t) =         alpha + beta * dlog Occ(t) + r(t)
r(t) = rho * r(t-1) + u(t), u correlated with the demand innovation.

Nominal mode is the fallback when no CPI history is loaded; alpha then carries
the market's average nominal drift. beta is constrained non-negative and shrunk
toward its prior. numpy only.
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

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("mode", "alpha", "beta", "beta_hat", "se_beta", "w_beta", "rho", "rho_hat",
                                               "w_rho", "sigma_u", "sigma_u_hat", "corr", "corr_hat", "w_corr", "years",
                                               "last_r", "last_year", "notes")}


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
    alpha = float((y - beta * x).mean())
    r = pd.Series(y - alpha - beta * x, index=years)

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
    return AdrModel(mode, alpha, float(beta), beta_hat, se_beta, w_beta, rho, rho_hat, w_rho, float(sigma_u),
                    sigma_u_hat, corr, corr_hat, w_corr, years, float(r.iloc[-1]) if len(r) else 0.0, last_year, notes)
