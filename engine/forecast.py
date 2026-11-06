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
from engine.drivers.apply import build_effects
from engine.drivers.validate import check_cards, parse_cards
from engine.explain import build_explanation
from engine.history.anchor import anchor_window, apply_anchor, compute_anchor, first_clean_year
from engine.settings import apply_settings
import dataclasses
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


def load_driver_priors(config_dir=None) -> dict:
    p = (config_dir or CONFIG_DIR) / "driver_priors.yaml"
    return load_yaml(p) if p.exists() else {"event_status_ladder": {}, "include_proposed": True}


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


ATTRIBUTION_PATHS = 4000


def run_forecast(res: IngestResult, cfg: MarketConfig, priors: dict, ppri: dict, macro: dict,
                 n_paths: int | None = None, seed: int | None = None, backtest_paths: int | None = None,
                 drivers: list[dict] | None = None, settings: dict | None = None, driver_priors: dict | None = None,
                 cache: dict | None = None, attribution: bool = True) -> dict:
    """drivers: raw driver cards (dicts). settings: user adjustments (see engine/settings.py).
    cache: a dict reused across calls on the same data to skip the backtest when only settings change."""
    t0 = time.time()
    n_paths = n_paths or cfg.simulation.paths
    seed = cfg.simulation.seed if seed is None else seed
    driver_priors = driver_priors if driver_priors is not None else load_driver_priors()
    applied = apply_settings(settings, ppri, macro, driver_priors)
    ppri, macro, driver_priors, knobs = applied.ppri, applied.macro, applied.driver_priors, applied.knobs
    cpi = cpi_history(macro)
    covid = cfg.estimation.covid_years
    hist = analyse_history(res.monthly_spliced, res.growth_sample, cfg, cpi)

    # The backtest tests the baseline engine on history; user settings do not change it, so it is cached.
    basis, a_start = knobs["anchor_basis"], knobs["anchor_start"]
    bt_key = (res.run_id, seed, backtest_paths, bool(cpi), basis, a_start)
    if cache is not None and cache.get("bt_key") == bt_key:
        bt = cache["bt"]
    else:
        bt = run_backtest(res.monthly_spliced, res.growth_sample, hist.annual, cfg, priors, seed, backtest_paths, cpi=cpi,
                          anchor_basis=basis, anchor_start=a_start, anchor_min_start=first_clean_year(res.breaks))
        if cache is not None:
            cache.update({"bt_key": bt_key, "bt": bt})
    include_covid = bt.chosen.startswith("with_covid")
    resid = bt.chosen.split("/")[1]
    dm = fit_demand(res.growth_sample, priors, covid, include_covid, resid)
    dm_alt = fit_demand(res.growth_sample, priors, covid, not include_covid, resid)
    am = fit_adr(hist.annual, dm, res.growth_sample, priors, cpi)
    fitted = {"demand_mu": dm.mu, "adr_alpha": am.alpha, "adr_beta": am.beta, "ceiling": hist.ceiling["annual"]}
    shock_drag0 = dm.expected_shock_drag()
    clean = first_clean_year(res.breaks)
    win = anchor_window(list(hist.annual["year"]), int(hist.annual["year"].max()), set(covid), a_start,
                        None if a_start is None else int(hist.annual["year"].max()) - a_start, clean)
    anchor = compute_anchor(hist.annual, win[0], win[1], cpi) if win else None
    if a_start is not None and anchor is not None and anchor.start != a_start:
        applied.adjustments.append(f"Long-run CAGR start moved from {a_start} to {anchor.start} (COVID years, a data break, or too short a window).")
    am_alt = dataclasses.replace(am)
    anchor_info = None
    if anchor is not None:
        dm_fit, am_fit = dataclasses.replace(dm), dataclasses.replace(am)
        if basis == "long_run_cagr":
            anchor_info = apply_anchor(dm, am, anchor, am.mode == "cpi")
            apply_anchor(dm_alt, am_alt, anchor, am.mode == "cpi")
            dm_other, am_other = dm_fit, am_fit
        else:
            dm_other, am_other = dataclasses.replace(dm), dataclasses.replace(am)
            apply_anchor(dm_other, am_other, anchor, am.mode == "cpi")
    else:
        dm_other, am_other = None, None
    # User biases are applied after anchoring and reported as adjustments.
    for m_ in (dm, dm_alt, dm_other):
        if m_ is None:
            continue
        m_.mu += knobs["trend_bias"]
        m_.p_scale_moderate = knobs["shock_mult_moderate"]
        m_.p_scale_severe = knobs["shock_mult_severe"]
    for a_ in (am, am_alt, am_other):
        if a_ is None:
            continue
        a_.alpha += knobs["drift_bias"]
        if knobs["pricing_power"] is not None:
            a_.beta = max(0.0, float(knobs["pricing_power"]))
    ceiling = dict(hist.ceiling)
    if knobs["ceiling"] is not None:
        ceiling["annual"] = float(knobs["ceiling"])

    last = res.monthly["period_start"].max()
    y0 = int(last.year)
    months = pd.date_range(last + pd.DateOffset(months=1), pd.Timestamp(year=y0 + cfg.horizon_years, month=12, day=1), freq="MS")

    use_cpi = am.mode == "cpi"
    if cpi and not use_cpi:
        raise ValueError("CPI history loaded but the ADR model is not in CPI mode")

    gs = res.growth_sample
    sg = gs.loc[gs["use_supply"], "g_supply"].dropna()
    hist_supply_growth = float(np.expm1(sg.median())) if len(sg) >= 5 else None
    # Supply trend: the long-run supply CAGR (user decision), falling back to the median growth.
    supply_trend = anchor.supply if anchor is not None else hist_supply_growth
    hist_supply_used = float(knobs["unannounced_rate"]) if knobs["unannounced_rate"] is not None else supply_trend

    # Drivers: parse, check, and turn into per-path effects.
    years_all = [y0 + i for i in range(cfg.horizon_years + 1)]
    cards, driver_issues = parse_cards(drivers)
    keep, more = check_cards(cards, years_all, driver_priors, driver_priors.get("include_proposed", True))
    driver_issues += more
    base_rooms = float(res.monthly.sort_values("period_start")["available_rooms"].iloc[-1])
    ladder = driver_priors.get("event_status_ladder", {})
    cutoff = int(last.month)

    def effects(card_list, n):
        return build_effects(card_list, years_all, n, seed, base_rooms, ladder, cutoff,
                             shock_cap=float(driver_priors.get("shock_multiplier_cap", 5.0)))

    def simulate(model, k, trace=None, n=None, levels=None, adr_model=None):
        n = n or n_paths
        adr_m = adr_model or am
        rng = np.random.default_rng(seed)
        sup = simulate_supply(res.pipeline, res.monthly, months, ppri, n, rng, hist_supply_used, covid)
        cp = cpi_paths(macro, [y0 + i for i in range(1, cfg.horizon_years + 1)], n, rng) if use_cpi else None
        c0 = cpi_rate(macro, y0) if use_cpi else 0.0
        if use_cpi and (cp is None or c0 is None):
            raise ValueError("CPI mode needs inflation for the stub year and every forecast year")
        return sup, simulate_baseline(res.monthly_spliced, hist.annual, model, adr_m, sup, ceiling, cfg.horizon_years,
                                      n, rng, covid, k, cp, c0, trace, levels)

    trace: dict = {}
    widen = bt.spread_by_h[:cfg.horizon_years] if (bt.spread_by_h and knobs["horizon_widening"]) else [1.0] * cfg.horizon_years
    spread_arr = np.asarray(widen, float) * knobs["spread_multiplier"]
    eff = effects(keep, n_paths)
    sup, paths = simulate(dm, spread_arr, trace, levels=eff.as_levels())
    summary = summarise(paths)
    _, paths_alt = simulate(dm_alt, spread_arr, levels=eff.as_levels(), adr_model=am_alt)
    basis_sens = []
    if dm_other is not None:
        _, paths_other = simulate(dm_other, spread_arr, levels=eff.as_levels(), adr_model=am_other)
        for a, b in zip(summarise(paths), summarise(paths_other)):
            basis_sens.append({"year": a["year"], **{m: {"default_p50": a[m]["p50"], "alternative_p50": b[m]["p50"],
                                                        "difference": a[m]["p50"] - b[m]["p50"]}
                                                    for m in ("occ_growth", "adr_growth", "revpar_growth")}})

    # Attribution by removal: each driver's effect on the mean, median and spread of growth.
    attrib = []
    if attribution and keep:
        n_a = min(n_paths, ATTRIBUTION_PATHS)
        _, p_all = simulate(dm, spread_arr, n=n_a, levels=effects(keep, n_a).as_levels())
        g_all = p_all.growth()
        targets = [("all", "All drivers together", [])] + [(c.id, c.title, [c.id]) for c in keep]
        for cid, title, drop in targets:
            subset = [c for c in keep if c.id not in drop] if drop else []
            _, p_wo = simulate(dm, spread_arr, n=n_a, levels=effects(subset, n_a).as_levels())
            g_wo = p_wo.growth()
            row = {"id": cid, "title": title, "years": []}
            for j, y in enumerate(p_all.years):
                row["years"].append({"year": y, **{
                    f"{mname}_{stat}": float(fn(g_all[mname][:, j]) - fn(g_wo[mname][:, j]))
                    for mname in ("occ_growth", "adr_growth", "revpar_growth")
                    for stat, fn in (("mean", np.mean), ("p50", np.median), ("sd", np.std))}})
            attrib.append(row)
    summary_alt = summarise(paths_alt)
    sens = []
    for a, b in zip(summary, summary_alt):
        sens.append({"year": a["year"], **{m: {"default_p50": a[m]["p50"], "alternative_p50": b[m]["p50"],
                                              "difference": a[m]["p50"] - b[m]["p50"]}
                                          for m in ("occ_growth", "adr_growth", "revpar_growth", "demand_growth")}})
    positions = position_vs_history(summary, hist.growth_pcts, hist.annual, hist.occ_annual_max)
    cpi_path_out = {str(y): cpi_rate(macro, y) for y in range(y0 + 1, y0 + cfg.horizon_years + 1)} if use_cpi else None
    mm = res.monthly
    ytd_cur = mm[mm["period_start"].dt.year == y0]
    ytd_prev = mm[(mm["period_start"].dt.year == y0 - 1) & (mm["period_start"].dt.month <= int(last.month))]
    stub_ytd = None
    if len(ytd_cur) and len(ytd_prev) == len(ytd_cur):
        def _occ(x): return x["demand"].sum() / x["supply"].sum()
        def _adr(x): return x["revenue"].sum() / x["demand"].sum()
        stub_ytd = {"months": int(len(ytd_cur)), "occ": _occ(ytd_cur) / _occ(ytd_prev) - 1, "adr": _adr(ytd_cur) / _adr(ytd_prev) - 1,
                    "revpar": (ytd_cur["revenue"].sum() / ytd_cur["supply"].sum()) / (ytd_prev["revenue"].sum() / ytd_prev["supply"].sum()) - 1}
    explanation = build_explanation(paths, trace, sup, dm, am, hist, summary, positions, ceiling["annual"],
                                    hist_supply_growth, cpi_path_out, anchor=anchor, basis=basis,
                                    supply_trend=hist_supply_used, shock_drag0=shock_drag0,
                                    stub_ytd=stub_ytd, stub_info=paths.stub_monthly)

    proj = sup.projects.copy()
    if not proj.empty:
        proj = proj.merge(annual_open_probabilities(sup), on="project_id", how="left")

    gaps = [
        "CPI history not loaded: ADR is modelled in nominal terms and real ADR is not reported." if not cpi else None,
        "No national or regional reference series uploaded." if res.reference_monthly is None else None,
        "No historical pipeline snapshots: delivery probabilities and delays are assumptions." if res.pipeline is None
        or res.pipeline["snapshot_date"].nunique() <= 1 else None,
        "No forward-looking drivers loaded: this is the baseline only." if not keep else
        f"{len(keep)} driver(s) applied, {sum(1 for c in keep if c.status == 'proposed')} still proposed (not approved): provisional.",
        "Scenarios with explicit probabilities are not built yet (Phase E).",
    ]
    assumptions = [
        f"Occupancy ceiling {hist.ceiling['annual']:.1%}: {hist.ceiling['basis']}",
        "Pipeline stage probabilities, delays, removals, ramp-up and induced-demand shares from config/pipeline_priors.yaml (assumptions).",
        (f"Supply floor: available rooms never fall below the market's long-run supply trend ({hist_supply_used:.1%} a year"
         + (f", CAGR {anchor.start} to {anchor.end}" if anchor is not None and knobs["unannounced_rate"] is None else ", set by the user")
         + "). The pipeline drives supply above that trend; the gap below it is unannounced supply. Only pipeline rooms above the "
         "trend bring extra demand, because demand for trend supply is in the demand anchor.")
        if hist_supply_used is not None else "No supply trend applied (history too short).",
        (f"Baseline: long-run CAGRs {anchor.start} to {anchor.end} (RevPAR {anchor.revpar:.1%}, occupancy {anchor.occupancy:.1%}, "
         f"ADR {anchor.adr:.1%}, demand {anchor.demand:.1%}, supply {anchor.supply:.1%} a year). The demand trend adds back the "
         "expected drag of shocks so the average path matches the long-run rate.")
        if (anchor is not None and basis == "long_run_cagr") else
        "Baseline: demand and ADR trends fitted on normal years (user setting).",
        f"Induced demand from new rooms assumes they stabilise at the trailing 12-month occupancy ({sup.ref_occupancy:.1%}).",
        "Shock regime: shock years, recovery profiles and Beta priors from config/priors.yaml (assumptions) updated with the market's own shock count.",
        "The demand growth lag for the first full year uses the stub year's demand growth, which includes the effect of current closures.",
        "In the ADR equation the occupancy change is clipped to 1.5 times its historical range, so severe shocks do not imply implausible ADR falls.",
    ]
    out = {
        "meta": {"engine_version": __version__, "seed": seed, "paths": n_paths, "horizon_years": cfg.horizon_years,
                 "stub_year": y0, "base_year": y0 - 1, "last_actual_month": last.strftime("%Y-%m"),
                 "variant": bt.chosen, "spread": bt.spread, "spread_by_h": list(spread_arr), "calibrated": bt.calibrated,
                 "adr_mode": am.mode,
                 "status": "Provisional baseline: no drivers or scenarios", "seconds": None,
                 "currency": cfg.currency},
        "macro": {"adr_mode": am.mode,
                  "cpi_path": {str(y): cpi_rate(macro, y) for y in range(y0, y0 + cfg.horizon_years + 1)} if use_cpi else None,
                  "cpi_source": (macro.get("cpi_history") or {}).get("source"),
                  "cpi_projection_note": (macro.get("cpi_projections") or {}).get("assumption")},
        "summary": summary,
        "explanation": explanation,
        "adjustments": applied.adjustments,
        "anchor": {**(anchor.to_dict() if anchor else {}), "basis": basis, "applied": anchor_info,
                   "basis_sensitivity": {"default": basis,
                                         "alternative": "fitted_trend" if basis == "long_run_cagr" else "long_run_cagr",
                                         "by_year": basis_sens}},
        "settings_effective": {**knobs, "fitted": fitted, "unannounced_rate_used": hist_supply_used,
                               "spread_by_year": list(spread_arr)},
        "drivers": {"cards": [c.model_dump() for c in cards], "applied": eff.applied, "issues": driver_issues,
                    "attribution": attrib, "ladder": ladder},
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
                     "spread_by_h": list(spread_arr),
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
