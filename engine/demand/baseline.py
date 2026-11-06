"""Baseline demand: AR(1) on annual log demand growth plus a shock regime.

g(t) = mu + phi * (g(t-1) - mu) + e(t)                normal years
g(t) += d_k                                           k-th year of a shock profile

Normal years exclude shock years, their recovery years, COVID years (default),
break-contaminated years and partial years. Shock profiles come from the
market's own history. Parameters are shrunk toward config/priors.yaml with
precision weights, and every weight is reported. numpy only (runs in Pyodide).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

T_DF_GRID = (3, 4, 5, 6, 8, 10, 15, 20, 30, 60)


@dataclass
class ShockProfile:
    year: int
    cls: str                    # moderate | severe
    deviations: list[float]     # log growth minus mu, shock year first


@dataclass
class DemandModel:
    mu: float
    phi: float
    sigma: float
    mu_hat: float
    phi_hat: float
    sigma_hat: float
    se_mu: float
    se_phi: float
    w_mu: float
    w_phi: float
    w_sigma: float
    residuals: np.ndarray
    t_df: float
    residual_type: str
    normal_years: list[int]
    shock_profiles: list[ShockProfile]
    shock_years: list[int]
    recovery_years: list[int]
    exposure_years: int
    p_moderate: tuple[float, float]   # Beta posterior (a, b)
    p_severe: tuple[float, float]
    include_covid: bool
    notes: list[str] = field(default_factory=list)
    p_scale_moderate: float = 1.0     # user setting: multiplier on the moderate shock probability
    p_scale_severe: float = 1.0

    def p_mean(self, cls: str) -> float:
        a, b = self.p_moderate if cls == "moderate" else self.p_severe
        return a / (a + b)

    def summary(self) -> dict:
        return {
            "mu": self.mu, "phi": self.phi, "sigma": self.sigma, "mu_hat": self.mu_hat, "phi_hat": self.phi_hat,
            "sigma_hat": self.sigma_hat, "se_mu": self.se_mu, "se_phi": self.se_phi, "w_mu": self.w_mu,
            "w_phi": self.w_phi, "w_sigma": self.w_sigma, "t_df": self.t_df, "residual_type": self.residual_type,
            "n_normal": len(self.normal_years), "normal_years": self.normal_years, "shock_years": self.shock_years,
            "recovery_years": self.recovery_years, "exposure_years": self.exposure_years,
            "p_moderate": self.p_mean("moderate"), "p_severe": self.p_mean("severe"),
            "p_moderate_beta": list(self.p_moderate), "p_severe_beta": list(self.p_severe),
            "shock_profiles": [{"year": p.year, "class": p.cls, "deviations": p.deviations} for p in self.shock_profiles],
            "include_covid": self.include_covid, "notes": self.notes,
        }


def _shrink(est: float, se: float, prior_mean: float, prior_sd: float) -> tuple[float, float]:
    if not np.isfinite(se) or se <= 0:
        return (est, 1.0) if np.isfinite(est) else (prior_mean, 0.0)
    if not np.isfinite(est):
        return prior_mean, 0.0
    w = prior_sd ** 2 / (prior_sd ** 2 + se ** 2)
    return w * est + (1 - w) * prior_mean, w


def fit_student_t_df(z: np.ndarray) -> float:
    """MLE of Student-t degrees of freedom on a grid, scale matched to the sample sd."""
    z = np.asarray(z, float)
    z = z[np.isfinite(z)]
    if len(z) < 8:
        return 8.0
    sd = z.std(ddof=1)
    best, best_ll = 8.0, -np.inf
    for df in T_DF_GRID:
        s = sd * math.sqrt((df - 2) / df)
        ll = len(z) * (math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi) - math.log(s)) \
            - (df + 1) / 2 * np.log1p((z / s) ** 2 / df).sum()
        if ll > best_ll:
            best, best_ll = float(df), ll
    return best


def demand_series(sample: pd.DataFrame, include_covid: bool) -> pd.Series:
    """Log demand growth that estimation may see: usable years plus COVID years (always
    kept as shock evidence)."""
    ok = sample["use_demand"] | (sample["covid"] & sample["reason_demand"].str.startswith("COVID"))
    s = sample.loc[ok, ["year", "g_demand"]].set_index("year")["g_demand"].astype(float)
    return s.dropna()


def fit_demand(sample: pd.DataFrame, priors: dict, covid_years: list[int], include_covid: bool = False,
               residual_type: str = "bootstrap") -> DemandModel:
    pd_ = priors["demand"]
    ps = priors["shock"]
    g = demand_series(sample, include_covid)
    covid = set(covid_years)
    notes: list[str] = []
    base = g[~g.index.isin(covid)]
    med = float(base.median())
    rsd = float((base - med).abs().median() * 1.4826) or float(base.std(ddof=1))
    years = list(g.index)

    # 1. Shock years. With include_covid the COVID years are treated as ordinary observations.
    candidates = g if not include_covid else base
    shock_years = [int(y) for y, v in candidates.items() if v - med < -ps["threshold_robust_sd"] * rsd]
    prelim = base[~base.index.isin(shock_years)]
    mu_ref = float(prelim.mean()) if len(prelim) else med

    # 2. Profiles: shock year plus up to max_recovery_years following years, stopping at the next shock.
    profiles, recovery = [], []
    for y in shock_years:
        devs = [float(g[y] - mu_ref)]
        for k in range(1, ps["max_recovery_years"] + 1):
            yk = y + k
            if yk not in g.index or yk in shock_years:
                break
            devs.append(float(g[yk] - mu_ref))
            recovery.append(int(yk))
        cls = "severe" if devs[0] < ps["severe_log_deviation"] else "moderate"
        profiles.append(ShockProfile(int(y), cls, devs))

    # 3. Normal years.
    excluded = set(shock_years) | set(recovery)
    if not include_covid:
        excluded |= covid
    normal = g[~g.index.isin(excluded)]
    if len(normal) < 5:
        notes.append(f"Only {len(normal)} normal years; parameters lean on the priors.")

    # 4. mu: mean of normal years. phi: on consecutive normal pairs.
    mu_hat = float(normal.mean()) if len(normal) else float("nan")
    sd_n = float(normal.std(ddof=1)) if len(normal) > 1 else float("nan")
    se_mu = sd_n / math.sqrt(len(normal)) if len(normal) > 1 else float("nan")
    mu, w_mu = _shrink(mu_hat, se_mu, pd_["mu_log_growth"]["mean"], pd_["mu_log_growth"]["sd"])
    pairs = [(normal[y - 1], normal[y]) for y in normal.index if (y - 1) in normal.index]
    if len(pairs) >= 3:
        x = np.array([p[0] for p in pairs]) - mu
        yv = np.array([p[1] for p in pairs]) - mu
        phi_hat = float((x * yv).sum() / (x * x).sum())
        resid0 = yv - phi_hat * x
        s2 = float((resid0 ** 2).sum() / max(len(pairs) - 1, 1))
        se_phi = math.sqrt(s2 / (x * x).sum())
    else:
        phi_hat, se_phi = float("nan"), float("nan")
        notes.append("Fewer than 3 consecutive normal-year pairs; phi set to its prior.")
    phi, w_phi = _shrink(phi_hat, se_phi, pd_["phi"]["mean"], pd_["phi"]["sd"])
    phi = float(np.clip(phi, pd_["phi"]["lower"], pd_["phi"]["upper"]))

    # 5. Residuals with the shrunk parameters.
    if len(pairs) >= 5:
        x = np.array([p[0] for p in pairs])
        yv = np.array([p[1] for p in pairs])
        resid = yv - mu - phi * (x - mu)
    else:
        resid = (normal - mu).to_numpy()
    sigma_hat = float(np.sqrt((resid ** 2).sum() / max(len(resid) - 1, 1)))
    n0 = pd_["residual_sd"]["pseudo_obs"]
    w_sigma = len(resid) / (len(resid) + n0)
    sigma = w_sigma * sigma_hat + (1 - w_sigma) * pd_["residual_sd"]["mean"]
    t_df = fit_student_t_df(resid)

    # 6. Shock probabilities: Beta prior updated with counts over the exposure years.
    exposure = len(years) if not include_covid else len([y for y in years if y not in covid])
    k_mod = sum(1 for p in profiles if p.cls == "moderate")
    k_sev = sum(1 for p in profiles if p.cls == "severe")
    pm = (ps["moderate"]["a"] + k_mod, ps["moderate"]["b"] + exposure - k_mod)
    pv = (ps["severe"]["a"] + k_sev, ps["severe"]["b"] + exposure - k_sev)
    if k_sev == 0:
        notes.append("No severe shock in the history; the severe class has no profile and is not simulated.")
    if k_mod == 0:
        notes.append("No moderate shock in the history; the moderate class has no profile and is not simulated.")

    return DemandModel(
        mu=float(mu), phi=phi, sigma=float(sigma), mu_hat=mu_hat, phi_hat=phi_hat, sigma_hat=sigma_hat,
        se_mu=se_mu, se_phi=se_phi, w_mu=w_mu, w_phi=w_phi, w_sigma=w_sigma, residuals=resid - resid.mean(),
        t_df=t_df, residual_type=residual_type, normal_years=[int(y) for y in normal.index],
        shock_profiles=profiles, shock_years=shock_years, recovery_years=recovery, exposure_years=exposure,
        p_moderate=pm, p_severe=pv, include_covid=include_covid, notes=notes)


def draw_innovations(model: DemandModel, rng: np.random.Generator, size: tuple[int, ...]) -> np.ndarray:
    """Standardised innovations (unit variance)."""
    if model.residual_type == "student_t" or len(model.residuals) < 5:
        df = model.t_df
        return rng.standard_t(df, size=size) * math.sqrt((df - 2) / df)
    r = model.residuals / (model.residuals.std(ddof=1) or 1.0)
    return rng.choice(r, size=size, replace=True)


def simulate_shocks(model: DemandModel, rng: np.random.Generator, n_paths: int, n_years: int,
                    mult: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return (deviation[n_paths, n_years], class code: 0 none, 1 moderate, 2 severe at onset).

    mult [n_paths, n_years] scales the annual shock probability (drivers and user settings)."""
    dev = np.zeros((n_paths, n_years))
    onset = np.zeros((n_paths, n_years), dtype=np.int8)
    by_cls = {c: [p for p in model.shock_profiles if p.cls == c] for c in ("moderate", "severe")}
    p_mod = rng.beta(*model.p_moderate, size=n_paths) * model.p_scale_moderate if by_cls["moderate"] else np.zeros(n_paths)
    p_sev = rng.beta(*model.p_severe, size=n_paths) * model.p_scale_severe if by_cls["severe"] else np.zeros(n_paths)
    busy_until = np.zeros(n_paths, dtype=int)
    u = rng.random((n_paths, n_years))
    pick = rng.random((n_paths, n_years))
    for t in range(n_years):
        free = busy_until <= t
        m = mult[:, t] if mult is not None else 1.0
        ps, pm = np.minimum(p_sev * m, 0.5), np.minimum(p_mod * m, 0.9)
        sev = free & (u[:, t] < ps)
        mod = free & ~sev & (u[:, t] < ps + pm)
        for code, mask, cls in ((2, sev, "severe"), (1, mod, "moderate")):
            idx = np.nonzero(mask)[0]
            if len(idx) == 0:
                continue
            profs = by_cls[cls]
            choice = np.minimum((pick[idx, t] * len(profs)).astype(int), len(profs) - 1)
            for j, prof in enumerate(profs):
                rows = idx[choice == j]
                if len(rows) == 0:
                    continue
                for k, d in enumerate(prof.deviations):
                    if t + k < n_years:
                        dev[rows, t + k] += d
                busy_until[rows] = t + len(prof.deviations)
            onset[idx, t] = code
    return dev, onset
