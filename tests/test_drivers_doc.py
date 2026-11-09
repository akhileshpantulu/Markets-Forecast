"""The drivers document builder (python -m engine.drivers.doc)."""

from __future__ import annotations

import shutil

import yaml

from engine.config import REPO_ROOT
from engine.drivers.doc import build
from tests.test_drivers import card

MARKET = "paris-louvre-marais"


def _root(tmp_path):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    run = tmp_path / "runs" / MARKET
    run.mkdir(parents=True)
    return run


def test_merges_research_files_and_writes_yaml_and_doc(tmp_path):
    run = _root(tmp_path)
    ev = [{"claim": "The organiser announced the event.", "url": "https://example.org/a", "publisher": "Organiser",
           "published": "2026-10-01", "accessed": "2026-11-06"}]
    (run / "drivers_demand.yaml").write_text(yaml.safe_dump({"drivers": [
        card(id="concert", status="proposed", evidence=ev, probability=None, event_status="announced")]}))
    (run / "drivers_geopolitics.yaml").write_text(yaml.safe_dump([
        card(id="unrest", affects="shock_probability", timing={"start_year": 2027},
             magnitude={"low": 1.2, "mode": 1.5, "high": 2.0, "unit": "multiplier"}),
        card(id="bad", timing={"start_year": 2040})]))
    y, md, issues = build(MARKET, stub_year=2026, root=tmp_path)

    out = yaml.safe_load(y.read_text())
    assert [d["id"] for d in out["drivers"]] == ["concert", "unrest", "bad"]
    assert y.read_text().startswith("# Drivers for")
    assert any(i["id"] == "bad" and i["level"] == "error" for i in issues)

    text = md.read_text()
    assert "| Concert | Demand |" in text and "(not applied)" in text
    assert "80%" in text and "event status: announced, from the ladder" in text
    assert "[Organiser](https://example.org/a)" in text
    assert "1.2x to 2x" in text
    assert "runs/paris-louvre-marais/drivers_demand.yaml" in text
    assert "—" not in text                                  # no em dashes in generated prose


def test_output_file_is_not_merged_back_in(tmp_path):
    run = _root(tmp_path)
    (run / "drivers_demand.yaml").write_text(yaml.safe_dump({"drivers": [card(id="a")]}))
    build(MARKET, stub_year=2026, root=tmp_path)
    y, _, _ = build(MARKET, stub_year=2026, root=tmp_path)    # drivers.yaml must not match drivers_*.yaml
    assert [d["id"] for d in yaml.safe_load(y.read_text())["drivers"]] == ["a"]


def test_research_coverage_is_enforced(tmp_path):
    run = _root(tmp_path)
    (run / "drivers_demand.yaml").write_text(yaml.safe_dump({"drivers": [card(id="fair")]}))
    _, md, issues = build(MARKET, stub_year=2026, root=tmp_path)
    missing = [i for i in issues if i["id"].startswith("coverage:") and i["level"] == "error"]
    assert len(missing) > 40 and "NOT RESEARCHED" in md.read_text()
    items = yaml.safe_load((tmp_path / "config" / "research_checklist.yaml").read_text())["items"]
    cov = [{"item": it["id"], "status": "no_material_effect", "finding": "Checked; nothing found.",
            "sources": [{"publisher": "Organiser", "url": "https://example.org"}]} for it in items]
    cov[0] = {"item": items[0]["id"], "status": "card", "cards": ["fair"]}
    cov[1] = {"item": items[1]["id"], "status": "card", "cards": ["ghost"]}
    (run / "coverage_demand.yaml").write_text(yaml.safe_dump({"coverage": cov}))
    _, md, issues = build(MARKET, stub_year=2026, root=tmp_path)
    errs = [i for i in issues if i["id"].startswith("coverage:") and i["level"] == "error"]
    assert len(errs) == 1 and "ghost" in errs[0]["text"]
    assert f"{len(items)} of {len(items)} checklist items answered" in md.read_text()
    out = yaml.safe_load((run / "drivers.yaml").read_text())
    assert len(out["coverage"]) == len(items) and out["drivers"][0]["id"] == "fair"     # one file, like Cowork's
