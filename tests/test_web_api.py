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
    assert [s["id"] for s in info["slots"]] == ["performance", "pipeline", "airport", "reference"]
    assert [s["enabled"] for s in info["slots"]] == [True, True, False, False]
    assert any(m["slug"] == "paris-louvre-marais" for m in info["markets"])


def test_run_ingest_roundtrip(perf_bytes, pipe_bytes):
    out = json.loads(run_ingest(_files(perf_bytes, pipe_bytes), ADHOC, today="2026-07-20", paths=2000))
    assert out["meta"]["passed"] is True
    assert out["meta"]["last_month"] == "2026-06" and out["meta"]["stub_actual_months"] == 6
    assert len(out["monthly"]) == 258 and out["monthly"][0]["period"] == "2005-01"
    assert {r["year"] for r in out["annual"]} == set(range(2005, 2027))
    assert len(out["pipeline"]) == 7
    assert {c["id"] for c in out["checks"]["performance"]} >= {"P07", "P18"}
    assert out["breaks"] == []
    assert "Status: **PASSED**" in out["validation_md"]
    fc = out["forecast"]
    assert [r["year"] for r in fc["summary"]] == list(range(2026, 2032))
    assert "Baseline forecast" in fc["reports"]["baseline_md"] and "forecast_error" not in out


def test_disabled_slots_are_ignored(perf_bytes, pipe_bytes):
    files = _files(perf_bytes, pipe_bytes) + [{"slot": "airport", "filename": "x.xlsx", "data": b"junk"}]
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
