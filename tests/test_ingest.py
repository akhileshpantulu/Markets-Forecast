"""End-to-end ingestion on the synthetic CoStar-like fixtures."""

from __future__ import annotations

import pandas as pd

from engine.ingest.core import InputFile, ingest_files
from engine.ingest.report import render_validation
from engine.ingest.run import ingest_market
from engine.schema import PERFORMANCE_CORE, PIPELINE_CORE
from tests.conftest import PERF_FILE, PIPE_FILE, SYNTH_SLUG, TODAY


def _checks(res, group):
    return {c.id: c for c in res.checks[group]}


def test_synthetic_ingest_passes(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    assert res.passed, [c for cs in res.checks.values() for c in cs if c.status == "fail"] + res.fatal
    m = res.monthly
    assert set(PERFORMANCE_CORE) <= set(m.columns)
    assert m["period_start"].min() == pd.Timestamp("2005-01-01")
    assert m["period_start"].max() == pd.Timestamp("2026-06-01")
    assert len(m) == 21 * 12 + 6
    assert m["period_start"].is_monotonic_increasing
    pc = _checks(res, "performance")
    assert pc["P04"].status == "info"          # inventory-only rows 2003-2004 excluded
    assert pc["P11"].status == "pass"
    assert pc["P12"].status == "pass"
    assert pc["P17"].message.count("6 actual months") == 1


def test_annual_aggregation(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    a = res.annual.set_index("year")
    assert a.loc[2025, "complete"] and not a.loc[2026, "complete"]
    assert a.loc[2026, "months_observed"] == 6
    m = res.monthly[res.monthly["period_start"].dt.year == 2019]
    assert abs(a.loc[2019, "supply"] - m["supply"].sum()) < 1e-6
    assert abs(a.loc[2019, "occupancy"] - m["demand"].sum() / m["supply"].sum()) < 1e-12
    assert abs(a.loc[2019, "adr"] - m["revenue"].sum() / m["demand"].sum()) < 1e-9


def test_pipeline_mapping(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    p = res.pipeline.set_index("project_id")
    assert set(PIPELINE_CORE) <= set(res.pipeline.columns)
    assert p.loc["900001", "project_type"] == "renovation_reopening"
    assert p.loc["900001", "projected_open"] == pd.Timestamp("2027-03-01")   # from Year/Month Renov
    assert not p.loc["900001", "in_current_supply"]
    assert p.loc["900007", "project_type"] == "renovation_open"              # renovating while open
    assert p.loc["900007", "in_current_supply"]
    assert p.loc["900003", "projected_open"] == pd.Timestamp("2027-09-01")   # "Sept"
    assert p.loc["900006", "projected_open_basis"] == "year_only_default_month"
    assert p.loc["900006", "projected_open"] == pd.Timestamp("2029-07-01")
    assert (res.pipeline["snapshot_date"] == pd.Timestamp("2026-07-15")).all()
    lc = _checks(res, "pipeline")
    assert lc["L05"].status == "warn"   # one year-only date
    assert lc["L08"].status == "pass"   # 200 closed rooms reconcile with inventory gap


def test_run_id_is_deterministic(synth_files, synth_cfg, mappings):
    a = ingest_files(synth_files, synth_cfg, mappings, today=TODAY).run_id
    b = ingest_files(synth_files, synth_cfg, mappings, today=TODAY).run_id
    assert a == b and a.startswith("2026-07-20-")
    changed = [InputFile(f.category, f.filename, f.data + (b"x" if f.category == "pipeline" else b""))
               for f in synth_files]
    # Changing input bytes changes the run id (even if the file then fails to parse).
    from engine.ingest.core import compute_run_id
    assert compute_run_id(changed, mappings, TODAY) != a


def test_validation_report_renders(synth_files, synth_cfg, mappings):
    res = ingest_files(synth_files, synth_cfg, mappings, today=TODAY)
    md = render_validation(res, synth_cfg, TODAY)
    assert "Status: **PASSED**" in md
    assert "| Period | `period_start` |" in md
    assert "Repeated headers in the source, not mapped: Actual Owner." in md
    import re
    assert not re.search(r"\bnan\b", md, flags=re.IGNORECASE)


def test_missing_pipeline_is_fatal(perf_bytes, synth_cfg, mappings):
    res = ingest_files([InputFile("performance", PERF_FILE, perf_bytes)], synth_cfg, mappings, today=TODAY)
    assert not res.passed
    assert any("pipeline" in e for e in res.fatal)


def test_disk_run_writes_and_moves(synth_root):
    res, report = ingest_market(SYNTH_SLUG, root=synth_root, today=TODAY)
    assert res.passed
    assert report.exists() and report.name == "validation.md"
    assert report.parent.parent.name == SYNTH_SLUG
    processed = synth_root / "data" / "processed" / SYNTH_SLUG
    for name in ("performance_monthly.parquet", "performance_annual.parquet", "pipeline.parquet", "manifest.json"):
        assert (processed / name).exists(), name
    raw = synth_root / "data" / "raw" / SYNTH_SLUG / TODAY.isoformat()
    assert (raw / "performance" / PERF_FILE).exists()
    assert (raw / "pipeline" / PIPE_FILE).exists()
    assert (raw / "optional" / "notes.txt").exists()
    assert not (synth_root / "inbox" / SYNTH_SLUG / "performance" / PERF_FILE).exists()
    assert "Synthetic notes for tests." in report.read_text()
    back = pd.read_parquet(processed / "performance_monthly.parquet")
    pd.testing.assert_frame_equal(back.reset_index(drop=True), res.monthly.reset_index(drop=True),
                                  check_dtype=False)


def test_failed_run_does_not_move(synth_root):
    (synth_root / "inbox" / SYNTH_SLUG / "pipeline" / PIPE_FILE).unlink()
    res, report = ingest_market(SYNTH_SLUG, root=synth_root, today=TODAY)
    assert not res.passed
    assert "FAILED" in report.read_text()
    assert (synth_root / "inbox" / SYNTH_SLUG / "performance" / PERF_FILE).exists()
    assert not (synth_root / "data" / "processed" / SYNTH_SLUG / "performance_monthly.parquet").exists()


def test_fixture_generator_is_deterministic(tmp_path, perf_bytes, pipe_bytes):
    """The committed fixtures equal a fresh seeded regeneration (cell by cell)."""
    from engine.ingest.reader import read_table
    from tests.fixtures.make_synthetic import write_performance, write_pipeline
    write_performance(tmp_path / PERF_FILE)
    write_pipeline(tmp_path / PIPE_FILE)
    for name, committed in ((PERF_FILE, perf_bytes), (PIPE_FILE, pipe_bytes)):
        a = read_table(committed, filename=name)
        b = read_table(tmp_path / name)
        assert a.headers == b.headers
        assert a.rows == b.rows
