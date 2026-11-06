"""Anomaly guardrails: level-break detection, splicing, estimation sample, influence."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.config import SeriesBreak
from engine.history.anomalies import analyse_breaks, detect_level_breaks, splice_levels
from engine.history.sample import build_growth_sample, influence_check, mean_estimator, usable
from engine.ingest.aggregate import to_annual
from engine.ingest.core import ingest_files
from tests.conftest import TODAY


@pytest.fixture
def base(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    return res.monthly.copy()


def _step(monthly, measure, start, factor):
    m = monthly.copy()
    after = m["period_start"] >= pd.Timestamp(start)
    cols = {"adr": ["adr", "revenue", "revpar"], "demand": ["demand", "revenue", "occupancy", "revpar"],
            "supply": ["supply", "demand", "revenue", "inventory_rooms", "available_rooms", "closed_rooms"]}[measure]
    for c in cols:
        m.loc[after, c] = m.loc[after, c] * factor
    return m


def test_clean_series_has_no_breaks(base, synth_cfg):
    assert detect_level_breaks(base, synth_cfg) == []


def test_january_adr_step_detected_and_excluded(base, synth_cfg):
    m = _step(base, "adr", "2012-01-01", 1.5)
    breaks = analyse_breaks(m, synth_cfg)
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.measure, b.start, b.affected_years, b.action) == ("adr", pd.Timestamp("2012-01-01"), [2012],
                                                                 "exclude_growth")
    assert b.confirmed and b.step == pytest.approx(0.5, abs=0.06)
    sample = build_growth_sample(to_annual(m), breaks, synth_cfg).set_index("year")
    assert not sample.loc[2012, "use_adr"]
    assert sample.loc[2012, "use_demand"] and sample.loc[2012, "use_occupancy"]   # ADR break leaves demand alone
    assert sample.loc[2013, "use_adr"]
    assert 2012 not in usable(sample.reset_index(), "adr").index


def test_splice_restores_comparability(base, synth_cfg):
    m = _step(base, "adr", "2012-01-01", 1.5)
    breaks = analyse_breaks(m, synth_cfg)
    sp = splice_levels(m, breaks)
    pre = sp["period_start"] < pd.Timestamp("2012-01-01")
    ratio = (sp.loc[pre, "adr"] / base.loc[pre, "adr"]).to_numpy()
    assert np.allclose(ratio, breaks[0].factor)
    assert sp.loc[pre, "spliced"].all() and not sp.loc[~pre, "spliced"].any()
    # identities still hold after splicing
    assert np.allclose(sp["revenue"] / sp["demand"], sp["adr"])
    assert np.allclose(sp["occupancy"] * sp["adr"], sp["revpar"])
    # input untouched
    assert np.allclose(m["adr"], _step(base, "adr", "2012-01-01", 1.5)["adr"])


def test_mid_year_step_affects_two_years(base, synth_cfg):
    m = _step(base, "adr", "2012-07-01", 1.6)
    breaks = analyse_breaks(m, synth_cfg)
    assert len(breaks) == 1 and breaks[0].start == pd.Timestamp("2012-07-01")
    assert breaks[0].affected_years == [2012, 2013]


def test_supply_break_contaminates_occupancy_and_demand(base, synth_cfg):
    m = _step(base, "supply", "2014-01-01", 1.4)
    breaks = analyse_breaks(m, synth_cfg)
    assert {b.measure for b in breaks} >= {"supply"}
    s = build_growth_sample(to_annual(m), breaks, synth_cfg).set_index("year")
    assert not s.loc[2014, "use_supply"] and not s.loc[2014, "use_occupancy"] and not s.loc[2014, "use_demand"]


def test_reversing_shock_is_not_a_break(base, synth_cfg):
    """A uniform 12-month drop that recovers the next year is a shock, not a break."""
    m = base.copy()
    win = (m["period_start"] >= "2011-01-01") & (m["period_start"] < "2012-01-01")
    for c in ("adr", "revenue", "revpar"):
        m.loc[win, c] = m.loc[win, c] * 0.6
    assert detect_level_breaks(m, synth_cfg) == []


def test_recent_step_is_flagged_for_review_not_excluded(base, synth_cfg):
    m = _step(base, "adr", "2025-07-01", 1.5)
    breaks = analyse_breaks(m, synth_cfg)
    assert len(breaks) == 1
    assert breaks[0].action == "review" and not breaks[0].confirmed
    s = build_growth_sample(to_annual(m), breaks, synth_cfg).set_index("year")
    assert s.loc[2025, "use_adr"]
    assert splice_levels(m, breaks)["spliced"].sum() == 0


def test_user_override_keeps_observation(base, synth_cfg):
    cfg = synth_cfg.model_copy(deep=True)
    cfg.estimation.series_breaks = [SeriesBreak(measure="adr", year=2012, action="keep", decided_by="user")]
    m = _step(base, "adr", "2012-01-01", 1.5)
    breaks = analyse_breaks(m, cfg)
    assert breaks[0].action == "keep" and breaks[0].decided_by == "user"
    s = build_growth_sample(to_annual(m), breaks, cfg).set_index("year")
    assert s.loc[2012, "use_adr"]


def test_reference_series_marks_shared_breaks(base, synth_cfg):
    m = _step(base, "adr", "2012-01-01", 1.5)
    shared = analyse_breaks(m, synth_cfg, reference_monthly=m)
    alone = analyse_breaks(m, synth_cfg, reference_monthly=base)
    assert shared[0].in_reference is True and alone[0].in_reference is False


def test_covid_years_marked_for_sensitivity(base, synth_cfg):
    s = build_growth_sample(to_annual(base), [], synth_cfg)
    row = s.set_index("year").loc[2021]
    assert not row["use_demand"] and row["reason_demand"].startswith("COVID")
    assert 2021 in usable(s, "demand", include_covid=True).index
    assert 2021 not in usable(s, "demand").index


def test_partial_year_never_used(base, synth_cfg):
    s = build_growth_sample(to_annual(base), [], synth_cfg).set_index("year")
    assert not s.loc[2026, "use_adr"] and s.loc[2026, "reason_adr"] == "partial year"


def test_influence_flags_injected_anomaly():
    rng = np.random.default_rng(0)
    years = list(range(2000, 2020))
    s = pd.Series(rng.normal(0.02, 0.03, len(years)), index=years)
    clean = influence_check(s, mean_estimator)
    assert clean.flagged_years() == []
    s.loc[2010] = 0.45
    hit = influence_check(s, mean_estimator)
    assert hit.flagged_years() == [2010]
    assert "2010 moves mean" in hit.flagged[0]


def test_guardrails_attached_to_ingest(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    assert res.breaks == []
    assert res.growth_sample is not None and set(res.influence) == {"demand", "occupancy", "adr"}
    assert res.monthly_spliced["spliced"].sum() == 0
