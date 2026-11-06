"""Drivers and user settings."""

from __future__ import annotations

import numpy as np
import pytest

from engine.drivers.apply import build_effects, profile
from engine.drivers.schema import DriverCard
from engine.drivers.validate import check_cards, parse_cards
from engine.forecast import load_driver_priors, load_inputs, run_forecast
from engine.settings import apply_settings, settings_schema
from tests.test_explain import CFG, PRI, PPRI, market

DP = load_driver_priors()
LADDER = DP["event_status_ladder"]
YEARS = [2026, 2027, 2028, 2029, 2030, 2031]


def card(**kw) -> dict:
    base = {"id": "evt", "title": "Concert", "category": "events", "affects": "demand",
            "timing": {"start_year": 2028}, "probability": 0.5,
            "magnitude": {"low": 1.0, "mode": 2.0, "high": 3.0, "unit": "pct_of_level"},
            "already_in_baseline": "none", "baseline_overlap_note": "One-off event, not in history.",
            "origin": "user"}
    base.update(kw)
    return base


def test_profiles():
    one = DriverCard.model_validate(card())
    assert list(profile(one, YEARS)) == [0, 0, 1, 0, 0, 0]
    temp = DriverCard.model_validate(card(timing={"start_year": 2027, "peak_year": 2028, "decay_years": 2}))
    assert list(np.round(profile(temp, YEARS), 3)) == [0, 0.5, 1, 0.667, 0.333, 0]
    step = DriverCard.model_validate(card(shape="step", timing={"start_year": 2027, "peak_year": 2028}))
    assert list(profile(step, YEARS)) == [0, 0.5, 1, 1, 1, 1]


def test_one_off_event_mean_and_payback():
    c = DriverCard.model_validate(card(probability=0.4))
    eff = build_effects([c], YEARS, 50000, 1, 5000.0, LADDER, 9)
    lvl = eff.demand[:, 2]
    assert (lvl > 0).mean() == pytest.approx(0.4, abs=0.01)
    assert lvl.mean() == pytest.approx(0.4 * np.log1p(0.02), rel=0.05)
    assert (eff.demand[:, 3] == 0).all()                     # level returns: next year pays it back


def test_ladder_fills_probability_and_flags_outliers():
    c = DriverCard.model_validate(card(probability=None, event_status="rumour"))
    eff = build_effects([c], YEARS, 20000, 1, 5000.0, LADDER, 9)
    assert eff.applied[0]["probability"] == pytest.approx(0.175)
    cards, _ = parse_cards([card(probability=0.9, event_status="rumour", origin="research")])
    _, issues = check_cards(cards, YEARS, DP)
    texts = " ".join(i["text"] for i in issues)
    assert "outside the rumour band" in texts and "No evidence" in texts


def test_validation_errors_exclude_cards():
    raw = [card(id="a", already_in_baseline="full", baseline_overlap_note="Recurring fair."),
           card(id="b", affects="supply"),                                  # wrong unit
           card(id="c", timing={"start_year": 2040}),
           card(id="d"), card(id="d"),
           {"id": "broken"}]
    cards, issues = parse_cards(raw)
    keep, more = check_cards(cards, YEARS, DP)
    assert [c.id for c in keep] == ["d"]
    assert any(i["id"] == "broken" and i["level"] == "error" for i in issues)
    assert any("Duplicate" in i["text"] for i in more)


def test_shock_probability_driver_raises_onset_rate():
    c = DriverCard.model_validate(card(id="unrest", affects="shock_probability", probability=1.0,
                                       timing={"start_year": 2027},
                                       magnitude={"dist": "fixed", "low": 3, "mode": 3, "high": 3, "unit": "multiplier"}))
    eff = build_effects([c], YEARS, 1000, 1, 5000.0, LADDER, 9)
    assert np.allclose(eff.shock_mult[:, 0], 3.0) and np.allclose(eff.shock_mult[:, 1:], 1.0)


@pytest.fixture(scope="module")
def grow():
    return market()


def _run(res, **kw):
    return run_forecast(res, CFG, PRI, PPRI, {}, n_paths=4000, backtest_paths=200, **kw)


def test_event_moves_its_year_and_reverses(grow):
    cache = {}
    base = _run(grow, cache=cache)
    ev = _run(grow, cache=cache, drivers=[card(probability=1.0, magnitude={"dist": "fixed", "low": 5, "mode": 5, "high": 5,
                                                                             "unit": "pct_of_level"})])
    d = {r["year"]: r["occ_growth"]["mean"] - b["occ_growth"]["mean"] for r, b in zip(ev["summary"], base["summary"])}
    assert d[2028] > 0.02 and d[2029] < -0.02 and abs(d[2027]) < 0.003
    att = {a["id"]: a for a in ev["drivers"]["attribution"]}
    assert att["evt"]["years"][2]["occ_growth_mean"] > 0.02
    br = {b["year"]: b for b in ev["explanation"]["bridge"]}
    assert br[2028]["demand"]["drivers"] == pytest.approx(np.log1p(0.05), abs=1e-6)
    assert "bt" in cache                                     # backtest reused across settings


def test_settings_apply_and_report(grow):
    cache = {}
    base = _run(grow, cache=cache)
    adj = _run(grow, cache=cache, settings={"demand.trend_bias": 0.01, "adr.drift_bias": -0.01,
                                            "pipeline.under_construction.p": 0.5})
    assert len(adj["adjustments"]) == 3
    assert adj["settings_effective"]["trend_bias"] == 0.01
    tb = lambda o: o["explanation"]["bridge"][2]["demand"]["trend"]
    assert tb(adj) - tb(base) == pytest.approx(0.01, abs=1e-12)              # the bias enters the trend exactly
    assert adj["summary"][3]["demand_growth"]["p50"] > base["summary"][3]["demand_growth"]["p50"]
    assert adj["summary"][3]["adr_growth"]["p50"] < base["summary"][3]["adr_growth"]["p50"]
    wide = _run(grow, cache=cache, settings={"uncertainty.spread_multiplier": 1.5})
    w = lambda o: o["summary"][4]["occ_growth"]["p90"] - o["summary"][4]["occ_growth"]["p10"]
    assert w(wide) > w(base) * 1.2


def test_settings_schema_defaults_match_config():
    ppri, macro, dp = PPRI, {}, DP
    sch = settings_schema(ppri, macro, dp)
    f = {x["key"]: x for g in sch for x in g["fields"]}
    assert f["pipeline.under_construction.p"]["default"] == 0.90
    assert f["pipeline.final_planning.p"]["default"] == 0.60
    assert f["pipeline.proposed.p"]["default"] == 0.40
    a = apply_settings({"pipeline.proposed.p": 0.4}, ppri, macro, dp)
    assert a.adjustments == []                                # unchanged values are not adjustments
