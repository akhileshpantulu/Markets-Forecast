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
    assert occ[-1] - occ[0] < 0.04
    for r in out["summary"][3:]:
        assert abs(r["occ_growth"]["p50"]) < 0.012
    e = out["explanation"]
    assert e["historical_supply_growth"] == pytest.approx(0.025, abs=0.003)
    ub = e["unannounced_by_year"]
    rem = 0.011 / 3                                     # mean removal rate from the triangular prior
    assert ub["2027"] == 0.0
    assert ub["2028"] == pytest.approx((0.025 + rem) / 2, abs=0.003)
    assert ub["2030"] == pytest.approx(0.025 + rem, abs=0.003)
    late = np.mean([r["supply_growth"]["mean"] for r in out["summary"][3:]])
    assert late == pytest.approx(0.025, abs=0.004)      # net supply back on the historical trend
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
