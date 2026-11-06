"""Phase B and C: history, models, supply, stub, simulation, backtest, orchestration."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from engine.adr.model import fit_adr
from engine.backtest.rolling import crps_sample, run_backtest
from engine.config import load_yaml, CONFIG_DIR
from engine.demand.baseline import fit_demand, simulate_shocks, fit_student_t_df
from engine.forecast import load_inputs, run_forecast
from engine.history.analysis import analyse_history, cycle_map
from engine.ingest.core import ingest_files
from engine.simulate.baseline import simulate_baseline, summarise
from engine.supply.pipeline import simulate_supply
from tests.conftest import TODAY

PRIORS = load_yaml(CONFIG_DIR / "priors.yaml")
PPRI = load_yaml(CONFIG_DIR / "pipeline_priors.yaml")


@pytest.fixture(scope="module")
def ing(request):
    from tests.conftest import FIXTURES, PERF_FILE, PIPE_FILE, SYNTH_SLUG
    from engine.config import load_market_config
    from engine.ingest.core import InputFile
    from engine.ingest.mapping import load_mappings
    cfg = load_market_config(SYNTH_SLUG, FIXTURES / "config")
    files = [InputFile("performance", PERF_FILE, (FIXTURES / PERF_FILE).read_bytes()),
             InputFile("pipeline", PIPE_FILE, (FIXTURES / PIPE_FILE).read_bytes())]
    return ingest_files(files, cfg, load_mappings(), today=TODAY), cfg


@pytest.fixture(scope="module")
def hist(ing):
    res, cfg = ing
    return analyse_history(res.monthly_spliced, res.growth_sample, cfg)


@pytest.fixture(scope="module")
def forecast(ing):
    res, cfg = ing
    pri, ppri, macro = load_inputs(cfg)
    return run_forecast(res, cfg, pri, ppri, macro, n_paths=3000, backtest_paths=400)


# ---------- history ----------
def test_history_tables(hist):
    assert hist.annual["year"].is_monotonic_increasing
    assert set(hist.growth_pcts["sample"]) == {"all", "ex_covid"}
    assert 0.5 < hist.ceiling["annual"] <= hist.ceiling["monthly_max"] <= 1.0
    assert hist.occ_annual_max <= hist.ceiling["annual"] + 1e-9 or hist.occ_annual_max < 1
    d = hist.decomposition
    assert np.allclose(d["dlog_occ"], d["from_demand"] + d["from_supply"])
    s = hist.seasonality
    assert abs(s["demand_share_recent"].sum() - 1) < 1e-9


def test_cycle_map_finds_drawdown():
    a = pd.DataFrame({"year": range(2000, 2008), "revpar": [100, 110, 90, 95, 105, 112, 120, 118]})
    c = cycle_map(a, "revpar")
    assert len(c) == 1
    r = c.iloc[0]
    assert (r.peak_year, r.trough_year, r.recovery_year) == (2001, 2002, 2005)
    assert r.depth == pytest.approx(90 / 110 - 1)


# ---------- demand ----------
def test_demand_recovers_known_parameters():
    rng = np.random.default_rng(0)
    mu, phi, sd = 0.02, 0.4, 0.03
    g = [mu]
    for _ in range(400):
        g.append(mu + phi * (g[-1] - mu) + rng.normal(0, sd))
    years = list(range(1600, 1600 + len(g)))
    sample = pd.DataFrame({"year": years, "g_demand": g, "use_demand": True, "covid": False, "reason_demand": ""})
    pri = {**PRIORS, "shock": {**PRIORS["shock"], "threshold_robust_sd": 10.0}}
    m = fit_demand(sample, pri, [])
    assert m.mu == pytest.approx(mu, abs=0.005)
    assert m.phi == pytest.approx(phi, abs=0.1)
    assert m.sigma == pytest.approx(sd, abs=0.005)
    assert 0 <= m.w_mu <= 1 and 0 <= m.w_phi <= 1


def test_shock_regime_and_probabilities(ing):
    res, cfg = ing
    m = fit_demand(res.growth_sample, PRIORS, cfg.estimation.covid_years)
    assert 0 < m.p_mean("moderate") < 1 and 0 <= m.p_mean("severe") < 1
    dev, onset = simulate_shocks(m, np.random.default_rng(1), 5000, 5)
    if m.shock_profiles:
        rate = (onset[:, 0] > 0).mean()
        expected = sum(m.p_mean(c) for c in ("moderate", "severe") if any(p.cls == c for p in m.shock_profiles))
        assert rate == pytest.approx(expected, abs=0.02)
    assert not set(m.normal_years) & (set(m.shock_years) | set(m.recovery_years))


def test_student_t_df_prefers_heavy_tails():
    rng = np.random.default_rng(3)
    assert fit_student_t_df(rng.standard_t(3, 3000)) <= 5
    assert fit_student_t_df(rng.standard_normal(3000)) >= 20


# ---------- ADR ----------
def test_adr_beta_non_negative(ing, hist):
    res, cfg = ing
    dm = fit_demand(res.growth_sample, PRIORS, cfg.estimation.covid_years)
    am = fit_adr(hist.annual, dm, res.growth_sample, PRIORS)
    assert am.beta >= 0 and am.mode == "nominal" and -0.9 <= am.corr <= 0.9
    pri = {**PRIORS, "adr": {**PRIORS["adr"], "beta_pricing_power": {"mean": -5.0, "sd": 0.01, "lower": 0.0}}}
    assert fit_adr(hist.annual, dm, res.growth_sample, pri).beta == 0.0


# ---------- supply ----------
def test_supply_paths(ing):
    res, cfg = ing
    months = pd.date_range("2026-07-01", "2031-12-01", freq="MS")
    sp = simulate_supply(res.pipeline, res.monthly, months, PPRI, 4000, np.random.default_rng(2))
    assert sp.rooms.shape == (4000, len(months))
    for pid, idx in sp.open_month.items():
        assert ((idx == -1) | ((idx >= 0) & (idx < len(months)))).all()
    p = sp.projects.set_index("project_id")
    assert (p["p_open_horizon"].between(0, 1)).all()
    # the proposed project opens less often than under-construction ones
    assert p.loc["900006", "p_open_horizon"] < p.loc["900003", "p_open_horizon"]
    # a renovating-while-open hotel adds no rooms; a reopening adds its rooms once open
    assert "900007" not in sp.open_month
    idx = sp.open_month["900001"]
    opened = idx[idx >= 0]
    assert len(opened) and (sp.rooms[idx >= 0, -1] > sp.base_rooms * 0.95).all()


# ---------- simulation ----------
def _sim(ing, hist, n=4000, seed=5):
    res, cfg = ing
    dm = fit_demand(res.growth_sample, PRIORS, cfg.estimation.covid_years)
    am = fit_adr(hist.annual, dm, res.growth_sample, PRIORS)
    rng = np.random.default_rng(seed)
    months = pd.date_range("2026-07-01", "2031-12-01", freq="MS")
    sp = simulate_supply(res.pipeline, res.monthly, months, PPRI, n, rng)
    return simulate_baseline(res.monthly_spliced, hist.annual, dm, am, sp, hist.ceiling, 5, n, rng, cfg.estimation.covid_years)


def test_occupancy_never_exceeds_ceiling(ing, hist):
    paths = _sim(ing, hist)
    assert (paths.occ[:, 1:] <= hist.ceiling["annual"] + 1e-12).all()
    assert np.isfinite(paths.adr).all() and (paths.adr > 0).all()


def test_stub_band_narrower_than_y1(ing, hist):
    s = summarise(_sim(ing, hist))
    width = lambda r: r["occ_growth"]["p90"] - r["occ_growth"]["p10"]
    assert width(s[0]) < width(s[1])
    w_adr = lambda r: r["adr_growth"]["p90"] - r["adr_growth"]["p10"]
    assert w_adr(s[0]) < w_adr(s[1])


def test_revpar_identity_path_by_path(ing, hist):
    paths = _sim(ing, hist)
    g = paths.growth()
    assert np.allclose(1 + g["revpar_growth"], (1 + g["occ_growth"]) * (1 + g["adr_growth"]))


def test_fixed_seed_reproduces(ing, hist):
    a, b = _sim(ing, hist, 1500, 9), _sim(ing, hist, 1500, 9)
    assert np.array_equal(a.occ, b.occ) and np.array_equal(a.adr, b.adr)
    c = _sim(ing, hist, 1500, 10)
    assert not np.array_equal(a.occ, c.occ)


# ---------- backtest ----------
def test_crps_sample():
    assert crps_sample(np.array([0.3]), 0.1) == pytest.approx(0.2)
    rng = np.random.default_rng(0)
    x = rng.standard_normal(200000)
    exact = 2 * (1 / math.sqrt(2 * math.pi)) - 1 / math.sqrt(math.pi)    # CRPS of N(0,1) at y=0
    assert crps_sample(x, 0.0) == pytest.approx(exact, abs=0.01)


def test_backtest_runs(ing, hist):
    res, cfg = ing
    bt = run_backtest(res.monthly_spliced, res.growth_sample, hist.annual, cfg, PRIORS, 1, n_paths=300)
    assert bt.origins and bt.chosen in set(bt.variants["variant"])
    assert set(bt.coverage["measure"]) == {"demand_growth", "occ_growth", "adr_growth", "revpar_growth"}
    assert bt.coverage["cov80"].between(0, 1).all()
    assert (bt.records["origin"] < bt.records["year"]).all()        # never forecasts the past


# ---------- orchestration ----------
def test_run_forecast_output(forecast):
    out = forecast
    json.dumps(out, allow_nan=False)
    assert [r["year"] for r in out["summary"]] == list(range(2026, 2032))
    assert out["meta"]["seed"] == 12345 and out["meta"]["adr_mode"] == "nominal"
    assert any("CPI" in g for g in out["gaps"])
    for r in out["summary"]:
        for k in ("occ_growth", "adr_growth", "revpar_growth"):
            q = r[k]
            assert q["p10"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p90"]
    assert len(out["covid_sensitivity"]["by_year"]) == 6


def test_run_forecast_regression(ing):
    res, cfg = ing
    pri, ppri, macro = load_inputs(cfg)
    a = run_forecast(res, cfg, pri, ppri, macro, n_paths=1500, backtest_paths=200)
    b = run_forecast(res, cfg, pri, ppri, macro, n_paths=1500, backtest_paths=200)
    a["meta"].pop("seconds"); b["meta"].pop("seconds")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
