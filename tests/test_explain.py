"""Explanation bridge, plausibility flags, unannounced supply and seasonal availability."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from engine.config import CONFIG_DIR, MarketConfig, load_yaml
from engine.forecast import run_forecast
from engine.ingest.aggregate import to_annual
from engine.ingest.core import IngestResult, apply_guardrails
from engine.ingest.validate import add_derived_columns

PRI = load_yaml(CONFIG_DIR / "priors.yaml")
PPRI = load_yaml(CONFIG_DIR / "pipeline_priors.yaml")
CFG = MarketConfig.model_validate({
    "slug": "growtown", "name": "Growtown", "geography_level": "market", "country": "Nowhere", "country_iso": "XX",
    "currency": "XXX", "simulation": {"paths": 3000, "export_paths": 100, "seed": 7},
})
SEASON = np.array([0.62, 0.66, 0.75, 0.82, 0.86, 0.90, 0.85, 0.74, 0.90, 0.88, 0.76, 0.66])


def market(supply_growth=0.025, seasonal_closure=False, seed=3) -> IngestResult:
    """Synthetic market: rooms grow steadily, occupancy stays flat on average, so demand and supply grow together."""
    rng = np.random.default_rng(seed)
    months = pd.date_range("2005-01-01", "2026-09-01", freq="MS")
    rows, level = [], 1.0
    for d in months:
        if d.month == 1:
            level *= np.exp(rng.normal(0, 0.02))
        t = (d.year - 2005) + (d.month - 1) / 12
        inv = 5000 * (1 + supply_growth) ** t
        open_share = 0.5 if (seasonal_closure and d.month in (11, 12, 1, 2, 3)) else 1.0
        supply = inv * open_share * d.days_in_month
        occ = min(0.95, 0.62 * SEASON[d.month - 1] / SEASON.mean() * level * np.exp(rng.normal(0, 0.06)))
        demand = supply * occ
        adr = 150 * 1.025 ** t * np.exp(rng.normal(0, 0.01))
        rows.append({"market": "growtown", "period_start": d, "period_type": "month", "supply": supply, "demand": demand,
                     "occupancy": occ, "adr": adr, "revpar": occ * adr, "revenue": demand * adr, "source_file": "synthetic",
                     "inventory_rooms": inv})
    m = add_derived_columns(pd.DataFrame(rows))
    res = IngestResult("growtown", m, to_annual(m), None, None, None, {}, [], None, "synthetic", [])
    apply_guardrails(res, CFG)
    return res


def run(res, ppri=PPRI):
    return run_forecast(res, CFG, PRI, ppri, {}, n_paths=3000, backtest_paths=200)


@pytest.fixture(scope="module")
def grow():
    return market()


def _no_unannounced():
    p = copy.deepcopy(PPRI)
    p["unannounced_supply"]["enabled"] = False
    return p


def test_without_unannounced_supply_occupancy_drifts_up(grow):
    """The failure mode behind 'incredibly aggressive' forecasts: thin pipeline, demand on its historical trend."""
    out = run(grow, _no_unannounced())
    occ = [r["occupancy"]["p50"] for r in out["summary"]]
    assert occ[-1] - occ[0] > 0.06
    assert any("Supply grows" in f["text"] for f in out["explanation"]["flags"])


def test_unannounced_supply_keeps_occupancy_near_history(grow):
    out = run(grow)
    occ = [r["occupancy"]["p50"] for r in out["summary"]]
    assert abs(occ[-1] - occ[0]) < 0.03
    for r in out["summary"][1:]:
        assert abs(r["occ_growth"]["p50"]) < 0.015
    e = out["explanation"]
    assert e["historical_supply_growth"] == pytest.approx(0.025, abs=0.003)
    assert out["anchor"]["supply"] == pytest.approx(0.025, abs=0.003)
    ub = e["unannounced_by_year"]
    rem = 0.011 / 3                                     # mean removal rate from the triangular prior
    # Supply floor at the long-run trend from the first full year: unannounced rooms replace removals and
    # add the trend growth, because the pipeline in this synthetic market is empty.
    for y in ("2027", "2028", "2030"):
        assert ub[y] == pytest.approx(0.025 + rem, abs=0.004)
    for r in out["summary"][1:]:
        assert r["supply_growth"]["mean"] == pytest.approx(0.025, abs=0.004)
    assert not any("Supply grows" in f["text"] for f in e["flags"])


def test_bridge_adds_up(grow):
    e = run(grow)["explanation"]
    for b in e["bridge"]:
        for part in ("demand", "adr", "supply"):
            comps = {k: v for k, v in b[part].items() if k != "total"}
            assert sum(comps.values()) == pytest.approx(b[part]["total"], abs=1e-9)
        assert b["occupancy_total"] == pytest.approx(b["demand"]["total"] - b["supply"]["total"], abs=1e-12)
    assert len(e["per_year"]) == 5 and e["drivers"]


def test_seasonal_closures_follow_the_pattern():
    res = market(seasonal_closure=True)
    out = run(res)
    si = out["explanation"]["seasonal_index"]
    assert min(si.values()) < 0.75 and max(si.values()) > 1.05
    # Base month is September (open). Without the seasonal index the first full year would carry
    # summer availability into winter and show a large supply jump.
    y1 = out["summary"][1]["supply_growth"]["p50"]
    assert abs(y1) < 0.04


def test_ceiling_flag_fires_when_paths_hit_the_cap():
    res = market(supply_growth=0.04)
    p = _no_unannounced()
    out = run(res, p)
    assert any("occupancy ceiling" in f["text"] for f in out["explanation"]["flags"])



def test_anchor_reproduces_long_run_cagr(grow):
    """With supply on its trend and no drivers, the forecast's average RevPAR growth is the long-run CAGR."""
    out = run(grow)
    a = out["anchor"]
    assert a["basis"] == "long_run_cagr" and a["start"] == 2005 and a["end"] == 2025
    assert np.log1p(a["revpar"]) == pytest.approx(np.log1p(a["occupancy"]) + np.log1p(a["adr"]), abs=1e-9)
    assert np.log1p(a["demand"]) == pytest.approx(np.log1p(a["supply"]) + np.log1p(a["occupancy"]), abs=1e-9)
    rec = out["explanation"]["reconciliation"]
    for r in rec:
        parts = sum(v for k, v in r.items() if k not in ("year", "total"))
        assert parts == pytest.approx(r["total"], abs=1e-9)                 # the reconciliation is exact
    late = np.mean([r["total"] for r in rec[2:]])
    assert late == pytest.approx(np.log1p(a["revpar"]), abs=0.012)


def test_trend_basis_setting_and_sensitivity(grow):
    out = run_forecast(grow, CFG, PRI, PPRI, {}, n_paths=3000, backtest_paths=200, settings={"anchor.basis": "fitted_trend"})
    assert out["anchor"]["basis"] == "fitted_trend"
    assert out["anchor"]["basis_sensitivity"]["alternative"] == "long_run_cagr"
    assert len(out["anchor"]["basis_sensitivity"]["by_year"]) == 6
    assert any("Trend basis" in a for a in out["adjustments"])
    variants = {v["variant"] for v in out["backtest"]["variants"]}
    assert any(v.endswith("/long_run_cagr") for v in variants)



def test_growing_market_is_not_seasonal():
    """Rooms that rise through every year (new openings) must not be read as seasonal closures."""
    from engine.supply.pipeline import seasonal_availability
    months = pd.date_range("2018-01-01", "2026-09-01", freq="MS")
    rooms = 5000 * 1.06 ** ((np.arange(len(months))) / 12)          # +6% a year, rising every month
    idx = seasonal_availability(pd.DataFrame({"period_start": months, "available_rooms": rooms}), {2020, 2021, 2022})
    assert all(v == 1.0 for v in idx.values())
    stepped = rooms.copy()
    stepped[(months.month >= 11) | (months.month <= 3)] *= 0.6                  # real winter closures still detected
    idx2 = seasonal_availability(pd.DataFrame({"period_start": months, "available_rooms": stepped}), {2020, 2021, 2022})
    assert idx2[1] < 0.85 and idx2[7] > 1.1



def test_anchor_window_rules():
    from engine.history.anchor import anchor_window
    yrs = list(range(2006, 2026))
    cov = {2020, 2021, 2022}
    assert anchor_window(yrs, 2025, cov, None, None) == (2006, 2025)
    assert anchor_window(yrs, 2025, cov, 2019, 6) == (2019, 2025)
    assert anchor_window(yrs, 2021, cov, 2019, 6) == (2013, 2019)        # rolling window, COVID end moved back
    assert anchor_window(yrs, 2025, cov, 2021, 4) == (2019, 2025)          # start inside COVID, window widened back
    assert anchor_window(yrs, 2008, cov, None, None) == (2006, 2008)
