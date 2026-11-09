"""Ledger engine: lines add up, RevPAR identity, supply and quality lines, calculators, registry, calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.ledger import calculators as C
from engine.ledger.calibrate import annual_panel, error_library, event_analogs, supply_response
from engine.ledger.registry import effective, load_registry, schema
from engine.ledger.report import render
from engine.ledger.run import run_ledger
from tests.test_explain import CFG, PPRI, market


@pytest.fixture(scope="module")
def res():
    return market()


@pytest.fixture(scope="module")
def out(res):
    return run_ledger(res, CFG, PPRI, {}, n_paths=1500)


def test_lines_add_up_and_revpar_identity(out):
    for y in out["years"]:
        assert sum(l["occ"] for l in y["lines"]) == pytest.approx(y["occ_growth"], abs=1e-12)
        assert sum(l["adr"] for l in y["lines"]) == pytest.approx(y["adr_growth"], abs=1e-12)
        assert y["revpar_growth"] == pytest.approx((1 + y["occ_growth"]) * (1 + y["adr_growth"]) - 1, abs=1e-12)
        assert y["range"]["revpar_growth"]["p10"] < y["range"]["revpar_growth"]["p90"]
        assert all(l["text"] and l["basis"] for l in y["lines"])


def test_baseline_is_long_run_cagr_with_flat_occupancy(out):
    b = out["baseline"]
    full = [y for y in out["years"] if not y["is_stub"]]
    base = full[0]["lines"][0]
    assert base["id"] == "baseline" and base["occ"] == 0.0
    assert (1 + base["occ"]) * (1 + base["adr"]) - 1 == pytest.approx(b["revpar"], abs=1e-12)
    hist = run_ledger(market(), CFG, PPRI, {}, n_paths=500, settings={"baseline.occupancy_split": "historical"})
    hb = [y for y in hist["years"] if not y["is_stub"]][0]["lines"][0]
    assert hb["occ"] == pytest.approx(b["occupancy"]) and any("baseline.occupancy_split" not in a for a in hist["adjustments"])


def test_supply_coefficient_override_moves_only_supply_lines(res, out):
    alt = run_ledger(res, CFG, PPRI, {}, n_paths=1500, settings={"supply.occ_response": -1.0})
    assert any("Occupancy % per 1% of supply growth" in a for a in alt["adjustments"])
    for a, b in zip(alt["years"], out["years"]):
        for la, lb in zip(a["lines"], b["lines"]):
            if la["id"] not in ("supply_volume", "supply_overlap", "ceiling"):
                assert la["occ"] == pytest.approx(lb["occ"]) and la["adr"] == pytest.approx(lb["adr"])


def _pipeline(tier: str) -> pd.DataFrame:
    rows = [{"project_id": f"p{i}", "name": f"Hotel {i}", "rooms": 400, "stage": "under_construction",
             "projected_open": pd.Timestamp(2027 + i % 2, 6, 1), "project_type": "new_build", "snapshot_date": pd.Timestamp(2026, 7, 1),
             "expansion_rooms": np.nan, "chain_scale": tier} for i in range(4)]
    return pd.DataFrame(rows)


def test_lower_tier_pipeline_dilutes_rate(res):
    pipe = _pipeline("Upscale")
    hi = res.__class__(**{**res.__dict__, "pipeline": pipe.assign(chain_scale="Luxury")})
    lo = res.__class__(**{**res.__dict__, "pipeline": pipe.assign(chain_scale="Economy")})
    s = {"quality.average_tier": "upscale"}
    mix = lambda o: sum(l["adr"] for y in o["years"] for l in y["lines"] if l["id"] == "supply_mix")  # noqa: E731
    assert mix(run_ledger(lo, CFG, PPRI, {}, n_paths=500, settings=s)) < 0 < mix(run_ledger(hi, CFG, PPRI, {}, n_paths=500, settings=s))


def test_driver_expected_value_and_payback(res):
    card = {"id": "fest", "title": "Festival", "category": "events", "affects": "adr", "timing": {"start_year": 2028},
            "probability": 0.5, "magnitude": {"dist": "fixed", "low": 2, "mode": 2, "high": 2, "unit": "pct_of_level"},
            "already_in_baseline": "none", "baseline_overlap_note": "New.", "origin": "user", "status": "approved"}
    o = run_ledger(res, CFG, PPRI, {}, n_paths=800, drivers=[card])
    line = {y["year"]: next((l for l in y["lines"] if l["id"] == "driver:fest"), None) for y in o["years"]}
    assert line[2028]["adr"] == pytest.approx(0.01) and line[2029]["adr"] == pytest.approx(-0.01)   # one-off pays back
    assert line[2027] is None


def test_event_calculator_bottom_up_and_analog():
    ctx = C.MarketContext(1_000_000, {m: 1 / 12 for m in range(1, 13)}, {m: 1 / 12 for m in range(1, 13)}, 200.0, "EUR")
    R = effective({}).v
    r = C.compute({"type": "event", "inputs": {"attendees": 100000, "out_of_town_share": 0.5, "nights": 2, "guests_per_room": 2,
                                                "hotel_share": 1.0, "capture_share": 1.0, "displacement_share": 0.0, "adr_compression": 0.1,
                                                "months": [6]}}, ctx, R, "x")
    assert r.demand[1] == pytest.approx(0.05)                     # 100k x 0.5 x 2 / 2 = 50k room nights = 5%
    assert r.adr[1] == pytest.approx(0.1 / 12) and len(r.working) == 4
    a = C.compute({"type": "event", "inputs": {"event_type": "mega_multi_sport", "months": [7, 8]}}, ctx, R, "y")
    assert a.adr[0] <= a.adr[1] <= a.adr[2] and "analog" in a.working[0]


def test_other_calculators():
    ctx = C.MarketContext(1_000_000, {m: 1 / 12 for m in range(1, 13)}, {m: 1 / 12 for m in range(1, 13)}, 200.0, "EUR")
    R = effective({}).v
    air = C.compute({"type": "air_capacity", "inputs": {"seats": 100000, "load_factor": 1, "visitor_share": 1, "nights": 2,
                                                        "guests_per_room": 2, "hotel_share": 1, "capture_share": 1}}, ctx, R, "a")
    assert air.demand[1] == pytest.approx(0.1)
    src = C.compute({"type": "source_market", "inputs": {"share_of_demand": 0.2, "driver_change": -0.1, "elasticity": 1.5}}, ctx, R, "s")
    assert src.demand[1] == pytest.approx(-0.03)
    tax = C.compute({"type": "price_change", "inputs": {"price_change": 0.05, "elasticity": -0.6, "absorbed_by_hotels": 0.5}}, ctx, R, "t")
    assert tax.demand[1] == pytest.approx(-0.015) and tax.adr[1] == pytest.approx(-0.025)
    with pytest.raises(ValueError):
        C.compute({"type": "nope"}, ctx, R, "n")


def test_registry_schema_and_report(out):
    reg = load_registry()
    groups = schema(reg)
    keys = {f["key"] for g in groups for f in g["fields"]}
    assert {"supply.occ_response", "quality.average_tier", "shocks.annual_probability"} <= keys
    assert all(f["status"] in {"fitted", "assumption", "user_decision", "default"} for g in groups for f in g["fields"])
    v = effective({"supply.occ_response": -0.9, "unknown.key": 1})
    assert v["supply.occ_response"] == -0.9 and len(v.adjustments) == 1 and v.version() != effective({}).version()
    md = render(out, "Synthetown")
    assert "## Headline" in md and "Long-run RevPAR CAGR" in md and "—" not in md


def _panel(n_sub=12, years=range(2000, 2020), seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sub):
        inv = 1000.0
        for y in years:
            g_inv = rng.normal(0.02, 0.03)
            inv *= np.exp(g_inv)
            occ = 0.75 * np.exp(-0.5 * (g_inv - 0.02) + rng.normal(0, 0.02))
            adr = 100 * np.exp(0.02 * (y - 2000) + rng.normal(0, 0.03))
            for mth in range(1, 13):
                sup = inv * 30
                rows.append({"slug": f"s{s}", "market": "M XXX", "slice": "All", "period": pd.Timestamp(year=y, month=mth, day=1),
                             "is_actual": True, "supply": sup, "demand": sup * occ, "revenue": sup * occ * adr, "inventory_rooms": inv,
                             "occupancy": occ, "adr": adr})
    return pd.DataFrame(rows)


def test_calibration_recovers_supply_response_and_builds_error_library():
    p = _panel()
    a = annual_panel(p)
    sr = supply_response(a)
    assert sr["g_occ"]["same_year"] == pytest.approx(-0.5, abs=0.15)
    lib, bt = error_library(a)
    assert set(lib["horizons"]) == {1, 2, 3, 4, 5} and len(lib["horizons"][1]["occ"]) == 7
    per, pooled = event_analogs(p, [{"id": "e", "name": "E", "type": "t", "year": 2010, "months": [6], "markets": ["M XXX"]}])
    assert per and "t" in pooled


def test_current_year_is_ytd_growth_plus_remaining_month_events(res):
    m = res.monthly
    last = m["period_start"].max()
    y0, k = last.year, last.month
    cur, prev = m[m["period_start"].dt.year == y0], m[(m["period_start"].dt.year == y0 - 1) & (m["period_start"].dt.month <= k)]
    occ = (cur["demand"].sum() / cur["supply"].sum()) / (prev["demand"].sum() / prev["supply"].sum()) - 1
    o = run_ledger(res, CFG, PPRI, {}, n_paths=500)
    stub = o["years"][0]
    assert stub["is_stub"] and stub["lines"][0]["occ"] == pytest.approx(occ, abs=1e-12) and len(stub["lines"]) == 1
    late = {"id": "late", "title": "Year-end event", "category": "events", "affects": "demand", "timing": {"start_year": y0, "months": [12]},
            "probability": 1.0, "magnitude": {"dist": "fixed", "low": 1, "mode": 1, "high": 1, "unit": "pct_of_level"},
            "already_in_baseline": "none", "baseline_overlap_note": "New.", "origin": "user", "status": "approved"}
    gone = {**late, "id": "gone", "title": "Last year's event that does not recur", "timing": {"start_year": y0, "months": [k + 1]},
            "magnitude": {"dist": "fixed", "low": -0.5, "mode": -0.5, "high": -0.5, "unit": "pct_of_level"}}
    o2 = run_ledger(res, CFG, PPRI, {}, n_paths=500, drivers=[late, gone])
    s2 = o2["years"][0]
    assert s2["occ_growth"] - stub["occ_growth"] == pytest.approx(0.005, abs=1e-9)


def test_blank_chain_scale_is_market_average_and_serialisable(res):
    import json
    pipe = _pipeline("Economy")
    pipe.loc[0, "chain_scale"] = np.nan
    o = run_ledger(res.__class__(**{**res.__dict__, "pipeline": pipe}), CFG, PPRI, {}, n_paths=300)
    row = next(p for p in o["supply"]["projects"] if p["project_id"] == "p0")
    assert row["chain_scale"] is None and row["tier"].startswith("Unknown")
    from engine.web_api import _json_safe
    json.dumps(_json_safe(o), allow_nan=False, default=str)


def test_long_run_cagr_starts_at_earliest_year(res, out):
    first = int(res.annual[res.annual["complete"]]["year"].min())
    assert out["baseline"]["start"] == first
    later = run_ledger(res, CFG, PPRI, {}, n_paths=300, settings={"baseline.start_year": first + 3})
    assert later["baseline"]["start"] == first + 3 and later["adjustments"]
