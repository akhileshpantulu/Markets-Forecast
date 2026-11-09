"""The browser entry point returns valid JSON with everything the page renders."""

from __future__ import annotations

import json

from engine.web_api import app_info, make_adhoc_config, peek_pipeline_geography, run_ingest
from tests.conftest import PERF_FILE, PIPE_FILE


def _files(perf_bytes, pipe_bytes):
    return [{"slot": "performance", "filename": PERF_FILE, "data": perf_bytes},
            {"slot": "pipeline", "filename": PIPE_FILE, "data": pipe_bytes}]


ADHOC = {"adhoc": {"name": "Synthetown, Old Quarter", "currency": "XXX", "costar_market": "Synthetown",
                   "costar_submarket": "Old Quarter"}}


def test_app_info_lists_slots_and_markets():
    info = json.loads(app_info())
    assert [s["id"] for s in info["slots"]] == ["performance", "pipeline", "drivers"]
    assert any(m["slug"] == "paris-louvre-marais" for m in info["markets"])


def test_run_ingest_roundtrip(perf_bytes, pipe_bytes):
    out = json.loads(run_ingest(_files(perf_bytes, pipe_bytes), ADHOC, today="2026-07-20", paths=2000))
    assert out["meta"]["passed"] is True
    assert out["meta"]["last_month"] == "2026-06" and out["meta"]["stub_actual_months"] == 6
    assert {c["id"] for c in out["checks"]["performance"]} >= {"P07", "P18"}
    assert out["breaks"] == []
    assert "Status: **PASSED**" in out["validation_md"]
    assert "forecast_error" not in out, out.get("forecast_error")
    fc = out["forecast"]
    assert [y["year"] for y in fc["years"]] == list(range(2026, 2032))
    assert fc["meta"]["engine"] == "ledger" and "# Forecast:" in fc["report_md"]
    assert {h["year"] for h in fc["history"]} == set(range(2005, 2027))
    assert len(fc["supply"]["projects"]) >= 1


def test_disabled_slots_are_ignored(perf_bytes, pipe_bytes):
    files = _files(perf_bytes, pipe_bytes) + [{"slot": "reference", "filename": "x.xlsx", "data": b"junk"}]
    out = json.loads(run_ingest(files, ADHOC, today="2026-07-20", forecast=False))
    assert out["meta"]["passed"] is True
    assert all(f["filename"] != "x.xlsx" for f in out["files"])


def test_peek_and_adhoc_config(pipe_bytes):
    geo = peek_pipeline_geography(pipe_bytes, PIPE_FILE)
    assert geo["market"] == ["Synthetown"] and geo["submarket"] == ["Old Quarter"]
    cfg = make_adhoc_config("Synthetown, Old Quarter", "XXX", "Synthetown", "Old Quarter")
    assert cfg.slug == "synthetown-old-quarter" and cfg.geography_level == "submarket"


def test_missing_file_reports_failure(perf_bytes):
    out = json.loads(run_ingest([{"slot": "performance", "filename": PERF_FILE, "data": perf_bytes}], ADHOC,
                                today="2026-07-20"))
    assert out["meta"]["passed"] is False and out["fatal"]


def test_drivers_file_settings_and_rerun(perf_bytes, pipe_bytes):
    import yaml
    from engine.web_api import rerun
    cards = [{"id": "fair", "title": "Trade fair", "category": "mice", "affects": "demand", "timing": {"start_year": 2028},
              "event_status": "announced", "magnitude": {"low": 1, "mode": 2, "high": 3, "unit": "pct_of_level"},
              "already_in_baseline": "none", "baseline_overlap_note": "New fair.", "origin": "user"}]
    files = _files(perf_bytes, pipe_bytes) + [{"slot": "drivers", "filename": "drivers.yaml",
                                               "data": yaml.safe_dump({"drivers": cards}).encode()}]
    out = json.loads(run_ingest(files, ADHOC, today="2026-07-20", paths=1500))
    fc = out["forecast"]
    assert fc["drivers"]["applied"][0]["probability"] == 0.8              # from the ladder: announced
    assert any(g["id"] == "pipeline" for g in out["settings_schema"])
    assert any(g["id"] == "supply" and g["fields"][0]["status"] for g in out["settings_schema"])
    again = json.loads(rerun({"supply.occ_response": -0.8}, [], 1500))["forecast"]
    assert again["adjustments"] and again["drivers"]["applied"][0]["id"] == "fair"
    none = json.loads(rerun({}, [], 1500, use_file_drivers=False))["forecast"]
    assert none["drivers"]["applied"] == []
