"""Baseline forecast run (Phases B and C): history, models, supply, simulation, backtest.

No forward-looking drivers or scenarios yet. Output is one JSON-ready dict.
Pure computation (no disk I/O) so the browser can call it.
"""

from __future__ import annotations

import math
import time
from datetime import date

import numpy as np
import pandas as pd

from engine import __version__
from engine.adr.model import fit_adr
from engine.backtest.rolling import run_backtest
from engine.config import CONFIG_DIR, MarketConfig, load_yaml
from engine.demand.baseline import fit_demand
from engine.explain import build_explanation
from engine.history.analysis import analyse_history
from engine.ingest.core import IngestResult
from engine.simulate.baseline import position_vs_history, simulate_baseline, summarise
from engine.supply.pipeline import annual_open_probabilities, simulate_supply


def _num(v):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if (math.isnan(v) or math.isinf(v)) else float(v)
    if isinstance(v, (pd.Timestamp, date)):
        return v.isoformat()[:10]
    if isinstance(v, np.ndarray):
        return [_num(x) for x in v.tolist()]
    if isinstance(v, dict):
        return {str(k): _num(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_num(x) for x in v]
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return [{k: _num(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


def load_inputs(cfg: MarketConfig, config_dir=None) -> tuple[dict, dict, dict]:
    d = config_dir or CONFIG_DIR
    priors = load_yaml(d / "priors.yaml")
    ppri = load_yaml(d / "pipeline_priors.yaml")
    macro_path = d / "macro" / f"{cfg.country_iso}.yaml"
    macro = load_yaml(macro_path) if macro_path.exists() else {}
    gen = d / "macro" / "generated" / f"{cfg.country_iso}.yaml"
    if gen.exists():
        g = load_yaml(gen)
        # Fetched, sourced public data replaces manual entries that are gaps or unverified.
        if (g.get("cpi_history") or {}).get("values"):
            macro["cpi_history"] = g["cpi_history"]
        if (g.get("cpi_projections") or {}).get("values"):
            macro["cpi_projections"] = g["cpi_projections"]
        if g.get("real_gdp_growth"):
            macro["real_gdp_growth"] = g["real_gdp_growth"]
    return priors, ppri, macro


def cpi_history(macro: dict) -> dict[int, float] | None:
    vals = (macro.get("cpi_history") or {}).get("values") or {}
    return {int(k): float(v) for k, v in vals.items()} or None


def cpi_rate(macro: dict, year: int) -> float | None:
    hist = cpi_history(macro) or {}
    if year in hist:
        return hist[year]
    proj = {int(v["year"]): float(v["rate"]) for v in (macro.get("cpi_projections") or {}).get("values", [])}
    if year in proj:
        return proj[year]
    if proj:
        beyond = (macro.get("cpi_projections") or {}).get("beyond_last_year") or {}
        return float(beyond["rate"]) if beyond.get("rate") is not None else proj[max(proj)]
    return None


def cpi_paths(macro: dict, years: list[int], n: int, rng: np.random.Generator) -> np.ndarray | None:
    """Inflation per path and year: the projected path plus independent normal noise."""
    rates = [cpi_rate(macro, y) for y in years]
    if any(r is None for r in rates):
        return None
    sd = float((macro.get("cpi_projections") or {}).get("sampling_sd", 0.008))
    return np.asarray(rates)[None, :] + rng.normal(0, sd, (n, len(years)))


def run_forecast(res: IngestResult, cfg: MarketConfig, priors: dict, ppri: dict, macro: dict,
                 n_paths: int | None = None, seed: int | None = None, backtest_paths: int | None = None) -> dict:
    t0 = time.time()
    n_paths = n_paths or cfg.simulation.paths
    seed = cfg.simulation.seed if seed is None else seed
    cpi = cpi_history(macro)
    covid = cfg.estimation.covid_years
    hist = analyse_history(res.monthly_spliced, res.growth_sample, cfg, cpi)

    bt = run_backtest(res.monthly_spliced, res.growth_sample, hist.annual, cfg, priors, seed, backtest_paths, cpi=cpi)
    include_covid = bt.chosen.startswith("with_covid")
    resid = bt.chosen.split("/")[1]
    dm = fit_demand(res.growth_sample, priors, covid, include_covid, resid)
    dm_alt = fit_demand(res.growth_sample, priors, covid, not include_covid, resid)
    am = fit_adr(hist.annual, dm, res.growth_sample, priors, cpi)

    last = res.monthly["period_start"].max()
    y0 = int(last.year)
    months = pd.date_range(last + pd.DateOffset(months=1), pd.Timestamp(year=y0 + cfg.horizon_years, month=12, day=1), freq="MS")

    use_cpi = am.mode == "cpi"
    if cpi and not use_cpi:
        raise ValueError("CPI history loaded but the ADR model is not in CPI mode")

    gs = res.growth_sample
    sg = gs.loc[gs["use_supply"], "g_supply"].dropna()
    hist_supply_growth = float(np.expm1(sg.median())) if len(sg) >= 5 else None

    def simulate(model, k, trace=None):
        rng = np.random.default_rng(seed)
        sup = simulate_supply(res.pipeline, res.monthly, months, ppri, n_paths, rng, hist_supply_growth, covid)
        cp = cpi_paths(macro, [y0 + i for i in range(1, cfg.horizon_years + 1)], n_paths, rng) if use_cpi else None
        c0 = cpi_rate(macro, y0) if use_cpi else 0.0
        if use_cpi and (cp is None or c0 is None):
            raise ValueError("CPI mode needs inflation for the stub year and every forecast year")
        return sup, simulate_baseline(res.monthly_spliced, hist.annual, model, am, sup, hist.ceiling, cfg.horizon_years,
                                      n_paths, rng, covid, k, cp, c0, trace)

    trace: dict = {}
    sup, paths = simulate(dm, bt.spread, trace)
    summary = summarise(paths)
    _, paths_alt = simulate(dm_alt, bt.spread)
    summary_alt = summarise(paths_alt)
    sens = []
    for a, b in zip(summary, summary_alt):
        sens.append({"year": a["year"], **{m: {"default_p50": a[m]["p50"], "alternative_p50": b[m]["p50"],
                                              "difference": a[m]["p50"] - b[m]["p50"]}
                                          for m in ("occ_growth", "adr_growth", "revpar_growth", "demand_growth")}})
    positions = position_vs_history(summary, hist.growth_pcts, hist.annual, hist.occ_annual_max)
    cpi_path_out = {str(y): cpi_rate(macro, y) for y in range(y0 + 1, y0 + cfg.horizon_years + 1)} if use_cpi else None
    explanation = build_explanation(paths, trace, sup, dm, am, hist, summary, positions, hist.ceiling["annual"],
                                    hist_supply_growth, cpi_path_out)

    proj = sup.projects.copy()
    if not proj.empty:
        proj = proj.merge(annual_open_probabilities(sup), on="project_id", how="left")

    gaps = [
        "CPI history not loaded: ADR is modelled in nominal terms and real ADR is not reported." if not cpi else None,
        "No national or regional reference series uploaded." if res.reference_monthly is None else None,
        "No historical pipeline snapshots: delivery probabilities and delays are assumptions." if res.pipeline is None
        or res.pipeline["snapshot_date"].nunique() <= 1 else None,
        "No forward-looking drivers or scenarios yet (Phases D and E). This is the baseline only.",
    ]
    assumptions = [
        f"Occupancy ceiling {hist.ceiling['annual']:.1%}: {hist.ceiling['basis']}",
        "Pipeline stage probabilities, delays, removals, ramp-up and induced-demand shares from config/pipeline_priors.yaml (assumptions).",
        (f"Unannounced supply: hotels not yet in the pipeline export fill the gap between the market's median historical supply "
         f"growth ({hist_supply_growth:.1%} a year; none in the first forecast year, half in the second, all from the third) and "
         "what the pipeline delivers each year. No extra induced demand.")
        if hist_supply_growth and hist_supply_growth > 0 else "No unannounced supply added (historical supply growth not positive or too short).",
        f"Induced demand from new rooms assumes they stabilise at the trailing 12-month occupancy ({sup.ref_occupancy:.1%}).",
        "Shock regime: shock years, recovery profiles and Beta priors from config/priors.yaml (assumptions) updated with the market's own shock count.",
        "The demand growth lag for the first full year uses the stub year's demand growth, which includes the effect of current closures.",
        "In the ADR equation the occupancy change is clipped to 1.5 times its historical range, so severe shocks do not imply implausible ADR falls.",
    ]
    out = {
        "meta": {"engine_version": __version__, "seed": seed, "paths": n_paths, "horizon_years": cfg.horizon_years,
                 "stub_year": y0, "base_year": y0 - 1, "last_actual_month": last.strftime("%Y-%m"),
                 "variant": bt.chosen, "spread": bt.spread, "calibrated": bt.calibrated, "adr_mode": am.mode,
                 "status": "Provisional baseline: no drivers or scenarios", "seconds": None,
                 "currency": cfg.currency},
        "macro": {"adr_mode": am.mode,
                  "cpi_path": {str(y): cpi_rate(macro, y) for y in range(y0, y0 + cfg.horizon_years + 1)} if use_cpi else None,
                  "cpi_source": (macro.get("cpi_history") or {}).get("source"),
                  "cpi_projection_note": (macro.get("cpi_projections") or {}).get("assumption")},
        "summary": summary,
        "explanation": explanation,
        "positions": positions,
        "covid_sensitivity": {"default": "exclude" if not include_covid else "include", "by_year": sens},
        "stub": paths.stub_monthly,
        "base": paths.base,
        "models": {"demand": dm.summary(), "demand_alternative": dm_alt.summary(), "adr": am.summary(),
                   "ceiling": hist.ceiling},
        "supply": {"base_available_rooms": sup.base_rooms, "reference_occupancy": sup.ref_occupancy,
                   "projects": _records(proj), "notes": sup.notes},
        "history": {
            "annual": _records(hist.annual), "growth_percentiles": _records(hist.growth_pcts),
            "cycles": _records(hist.cycles), "decomposition": _records(hist.decomposition),
            "seasonality": _records(hist.seasonality), "seasonality_shift": _num(hist.seasonality_shift),
            "occ_annual_max": hist.occ_annual_max, "occ_annual_min": hist.occ_annual_min, "real_adr": hist.real_adr,
            "notes": hist.notes},
        "backtest": {"origins": bt.origins, "chosen": bt.chosen, "spread": bt.spread, "calibrated": bt.calibrated,
                     "coverage": _records(bt.coverage), "benchmarks": _records(bt.benchmarks),
                     "variants": _records(bt.variants), "notes": bt.notes,
                     "records": _records(bt.records[(bt.records["variant"] == bt.chosen) & (bt.records["spread"] == bt.spread)]
                                         .drop(columns=["variant", "spread"])) if not bt.records.empty else []},
        "gaps": [g for g in gaps if g],
        "assumptions": assumptions,
        "notes": paths.notes + dm.notes + am.notes,
    }
    out["meta"]["seconds"] = round(time.time() - t0, 2)
    return _num(out)
