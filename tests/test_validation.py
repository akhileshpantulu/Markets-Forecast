"""Each hard and soft check fires on a deliberately corrupted copy of the fixture."""

from __future__ import annotations

import copy

import pytest

from engine.config import MarketConfig
from engine.ingest.mapping import (
    Mapping,
    MappingError,
    apply_performance_mapping,
    apply_pipeline_mapping,
    detect_mapping,
)
from engine.ingest.reader import read_table
from engine.ingest.validate import validate_performance
from tests.conftest import PERF_FILE, PIPE_FILE


@pytest.fixture
def perf_raw(perf_bytes):
    return read_table(perf_bytes, filename=PERF_FILE)


@pytest.fixture
def pipe_raw(pipe_bytes):
    return read_table(pipe_bytes, filename=PIPE_FILE)


def _run(raw, mappings, cfg):
    mapping = detect_mapping(raw, mappings, kind="performance")
    df, xc, errs = apply_performance_mapping(raw, mapping, cfg.slug)
    checks, _ = validate_performance(df, xc, errs, cfg)
    return {c.id: c for c in checks}


def _col(raw, name):
    return raw.headers.index(name)


def test_clean_fixture_has_no_failures(perf_raw, mappings, synth_cfg):
    checks = _run(perf_raw, mappings, synth_cfg)
    assert not [c for c in checks.values() if c.status == "fail"]
    assert checks["P18"].status == "pass"


def test_duplicate_month_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    raw.rows.insert(1, list(raw.rows[0]))
    assert _run(raw, mappings, synth_cfg)["P02"].status == "fail"


def test_gap_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    del raw.rows[30]
    c = _run(raw, mappings, synth_cfg)["P05"]
    assert c.status == "fail" and len(c.details) == 1


def test_occupancy_identity_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    raw.rows[5][_col(raw, "Occupancy")] *= 1.01
    assert _run(raw, mappings, synth_cfg)["P07"].status == "fail"


def test_adr_identity_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    raw.rows[5][_col(raw, "Revenue")] *= 1.01
    checks = _run(raw, mappings, synth_cfg)
    assert checks["P08"].status == "fail"
    assert checks["P11"].status == "warn"    # 12-month revenue no longer reconciles


def test_partial_row_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    raw.rows[3][_col(raw, "ADR")] = "-"
    assert _run(raw, mappings, synth_cfg)["P03"].status == "fail"


def test_unparseable_value_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    raw.rows[3][_col(raw, "Supply")] = "abc"
    assert _run(raw, mappings, synth_cfg)["P01"].status == "fail"


def test_occupancy_above_one_fails(perf_raw, mappings, synth_cfg):
    raw = copy.deepcopy(perf_raw)
    i = 4
    raw.rows[i][_col(raw, "Demand")] = raw.rows[i][_col(raw, "Supply")] * 1.2
    raw.rows[i][_col(raw, "Occupancy")] = 1.2
    assert _run(raw, mappings, synth_cfg)["P06"].status == "fail"


def test_level_break_detected(perf_raw, mappings, synth_cfg):
    """A uniform 50% ADR step from one January onward is flagged as a break."""
    raw = copy.deepcopy(perf_raw)
    ia, ir, iv, ip = (_col(raw, h) for h in ("ADR", "Revenue", "RevPAR", "Period"))
    for row in raw.rows:
        year = int(str(row[ip]).split()[-1])
        if year >= 2012 and row[ia] not in (None, "-"):
            row[ia] *= 1.5
            row[ir] *= 1.5
            row[iv] *= 1.5
    c = _run(raw, mappings, synth_cfg)["P18"]
    assert c.status == "warn"
    assert any(d.startswith("adr step") and "starting 2012-01" in d for d in c.details)


def test_missing_mapped_column_stops(perf_raw, mappings):
    raw = copy.deepcopy(perf_raw)
    i = _col(raw, "Under Construction Rooms")
    raw.headers[i] = "UC Rooms (renamed)"
    with pytest.raises(MappingError, match="missing"):
        detect_mapping(raw, mappings, kind="performance")


def test_unknown_headers_stop(perf_raw, mappings):
    raw = copy.deepcopy(perf_raw)
    raw.headers = [f"col{i}" for i in range(len(raw.headers))]
    with pytest.raises(MappingError, match="no mapping"):
        detect_mapping(raw, mappings)


def test_ambiguous_mapping_stops(perf_raw, mappings):
    clone = Mapping.model_validate({**mappings[0].model_dump(), "source": "clone"})
    with pytest.raises(MappingError, match="ambiguous"):
        detect_mapping(perf_raw, [mappings[0], clone])


def test_unknown_stage_stops(pipe_raw, mappings):
    raw = copy.deepcopy(pipe_raw)
    raw.rows[0][_col(raw, "Constr Status")] = "Imagined"
    mapping = detect_mapping(raw, mappings, kind="pipeline")
    with pytest.raises(MappingError, match="Imagined"):
        apply_pipeline_mapping(raw, mapping)


def test_config_rejects_unknown_keys():
    with pytest.raises(Exception):
        MarketConfig.model_validate({"slug": "x", "name": "x", "geography_level": "market", "country": "x",
                                     "country_iso": "XX", "currency": "EUR", "simulation": {"seed": 1},
                                     "typo_key": 1})
