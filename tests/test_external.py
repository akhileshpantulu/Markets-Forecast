"""External public data: parsers, macro build, CPI mode."""

from __future__ import annotations

import json

import numpy as np
import pytest

from engine.backtest.rolling import naive_cpi_path
from engine.external.build import build_country
from engine.external.parse import load, parse_ecb_csv, parse_imf, parse_ine, parse_jsonstat


JSONSTAT = {
    "version": "2.0", "class": "dataset", "id": ["freq", "geo", "time"], "size": [1, 2, 3],
    "dimension": {
        "freq": {"category": {"index": {"M": 0}}},
        "geo": {"category": {"index": {"FR": 0, "ES": 1}}},
        "time": {"category": {"index": {"2026-01": 0, "2026-02": 1, "2026-03": 2}}},
    },
    "value": {"0": 10.0, "1": 11.0, "2": 12.0, "3": 20.0, "5": 22.0},
    "status": {"2": "p"},
}


def test_parse_jsonstat():
    df = parse_jsonstat(JSONSTAT)
    assert len(df) == 5
    row = df[(df.geo == "ES") & (df.time == "2026-03")].iloc[0]
    assert row.value == 22.0
    assert df[(df.geo == "FR") & (df.time == "2026-03")].iloc[0].status == "p"
    assert df[(df.geo == "ES") & (df.time == "2026-02")].empty          # missing value stays missing
    lst = {**JSONSTAT, "value": [10.0, 11.0, 12.0, 20.0, None, 22.0], "dimension": {
        **JSONSTAT["dimension"], "time": {"category": {"index": ["2026-01", "2026-02", "2026-03"]}}}}
    assert len(parse_jsonstat(lst)) == 5


def test_parse_imf_ecb_ine():
    imf = {"values": {"PCPIPCH": {"FRA": {"2024": 2.3, "2025": 0.9}, "ESP": {"2025": 2.5}}}}
    df = parse_imf(imf)
    assert set(df.columns) == {"indicator", "country", "year", "value"} and len(df) == 3
    csv = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\nEXR.M.USD.EUR.SP00.A,M,USD,EUR,SP00,A,2026-01,1.08\n"
    e = parse_ecb_csv(csv)
    assert e.iloc[0]["currency"] == "USD" and e.iloc[0]["value"] == 1.08
    ine = [{"COD": "X1", "Nombre": "Total", "Data": [{"Anyo": 2026, "FK_Periodo": 1, "Valor": 5.0}]}]
    assert parse_ine(ine).iloc[0]["value"] == 5.0


def _write_source(out, sid, fmt, body, fetched="2026-11-08T05:17:00+00:00"):
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{sid}.body.json").write_text(json.dumps(body))
    (out / f"{sid}.meta.json").write_text(json.dumps({
        "id": sid, "url": "https://example.test/" + sid, "publisher": "International Monetary Fund", "title": "t",
        "format": fmt, "fetched_at": fetched, "http_status": 200, "bytes": 1, "sha256": "0" * 64, "body_file": f"{sid}.body.json"}))


def test_build_country_splits_history_and_projections(tmp_path):
    out, gen = tmp_path / "ext", tmp_path / "gen"
    vals = {str(y): 1.0 + (y - 2000) * 0.05 for y in range(1996, 2032)}
    _write_source(out, "imf_pcpipch_long", "imf_datamapper", {"values": {"PCPIPCH": {"FRA": vals}}})
    _write_source(out, "imf_ngdp_rpch", "imf_datamapper", {"values": {"NGDP_RPCH": {"FRA": {"2026": 0.7}, "USA": {"2026": 2.0}}}})
    path = build_country("FR", out, gen)
    import yaml
    doc = yaml.safe_load(path.read_text())
    hist = doc["cpi_history"]["values"]
    assert min(hist) == 1996 and max(hist) == 2025
    assert hist[2025] == pytest.approx(0.0225)
    proj = doc["cpi_projections"]["values"]
    assert proj[0]["year"] == 2026 and proj[-1]["year"] == 2031
    assert doc["cpi_history"]["source"]["accessed"] == "2026-11-08"
    assert doc["real_gdp_growth"]["values"]["USA"][2026] == pytest.approx(0.02)
    assert build_country("ZZ", out, gen) is None
    assert load("missing", out) == (None, None)


def test_naive_cpi_path_decays_to_target():
    p = naive_cpi_path({2022: 0.06}, 2022, 5, 20000, np.random.default_rng(0))
    m = p.mean(axis=0)
    assert m[0] == pytest.approx(0.02 + 0.04 * 0.5, abs=0.001)
    assert m[-1] < m[0] and abs(m[-1] - 0.02) < 0.003


def test_forecast_in_cpi_mode(ing_cpi):
    out = ing_cpi
    assert out["meta"]["adr_mode"] == "cpi" and out["macro"]["adr_mode"] == "cpi"
    assert not any("CPI" in g for g in out["gaps"])
    assert any("real_adr_growth" in r for r in out["history"]["annual"])
    assert out["macro"]["cpi_path"]["2027"] == pytest.approx(0.02)


@pytest.fixture(scope="module")
def ing_cpi():
    from engine.config import load_market_config
    from engine.forecast import load_inputs, run_forecast
    from engine.ingest.core import InputFile, ingest_files
    from engine.ingest.mapping import load_mappings
    from tests.conftest import FIXTURES, PERF_FILE, PIPE_FILE, SYNTH_SLUG, TODAY
    cfg = load_market_config(SYNTH_SLUG, FIXTURES / "config")
    files = [InputFile("performance", PERF_FILE, (FIXTURES / PERF_FILE).read_bytes()),
             InputFile("pipeline", PIPE_FILE, (FIXTURES / PIPE_FILE).read_bytes())]
    res = ingest_files(files, cfg, load_mappings(), today=TODAY)
    pri, ppri, _ = load_inputs(cfg)
    macro = {"cpi_history": {"values": {y: 0.018 for y in range(2000, 2026)}},
             "cpi_projections": {"values": [{"year": y, "rate": 0.02} for y in range(2026, 2032)], "sampling_sd": 0.005}}
    return run_forecast(res, cfg, pri, ppri, macro, n_paths=2000, backtest_paths=200)
