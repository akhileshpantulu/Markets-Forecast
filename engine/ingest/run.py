"""Disk layer: read inbox/<market>/, write processed data, validation.md, move raw files."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from engine.config import CONFIG_DIR, REPO_ROOT, MarketConfig, load_market_config, load_yaml
from engine.ingest.core import CATEGORIES, IngestResult, InputFile, ingest_files
from engine.ingest.mapping import load_mappings
from engine.ingest.report import render_validation


def collect_inbox(inbox: Path) -> list[InputFile]:
    files: list[InputFile] = []
    if not inbox.exists():
        return files
    for p in sorted(inbox.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(inbox)
        category = rel.parts[0] if len(rel.parts) > 1 else "unknown"
        files.append(InputFile(category, p.name, p.read_bytes()))
    return files


def write_outputs(res: IngestResult, cfg: MarketConfig, processed_dir: Path, run_dir: Path,
                  today: date) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    report = render_validation(res, cfg, today)
    (run_dir / "validation.md").write_text(report, encoding="utf-8")
    if not res.passed:
        return run_dir / "validation.md"
    res.monthly.to_parquet(processed_dir / "performance_monthly.parquet", index=False)
    res.annual.to_parquet(processed_dir / "performance_annual.parquet", index=False)
    if res.monthly_spliced is not None:
        res.monthly_spliced.to_parquet(processed_dir / "performance_monthly_spliced.parquet", index=False)
    if res.growth_sample is not None:
        res.growth_sample.to_parquet(processed_dir / "growth_sample.parquet", index=False)
    if res.pipeline is not None:
        res.pipeline.to_parquet(processed_dir / "pipeline.parquet", index=False)
    if res.reference_monthly is not None:
        res.reference_monthly.to_parquet(processed_dir / "reference_monthly.parquet", index=False)
        res.reference_annual.to_parquet(processed_dir / "reference_annual.parquet", index=False)
    manifest = {
        "market": res.market,
        "run_id": res.run_id,
        "processed_on": today.isoformat(),
        "files": [{"category": f.category, "filename": f.filename, "sha256": f.sha256, "mapping": f.mapping,
                   "snapshot_date": f.snapshot_date.isoformat() if f.snapshot_date else None} for f in res.files],
        "level_breaks": [b.to_dict() for b in res.breaks],
        "monthly_range": [res.monthly["period_start"].min().strftime("%Y-%m"),
                          res.monthly["period_start"].max().strftime("%Y-%m")],
    }
    (processed_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir / "validation.md"


def move_raw(inbox: Path, raw_root: Path, today: date) -> Path:
    dest = raw_root / today.isoformat()
    for cat in CATEGORIES:
        src = inbox / cat
        if not src.exists():
            continue
        for p in src.iterdir():
            if p.is_file() and not p.name.startswith("."):
                (dest / cat).mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), dest / cat / p.name)
    return dest


def ingest_market(slug: str, root: Path = REPO_ROOT, move: bool = True, today: date | None = None,
                  inbox: Path | None = None) -> tuple[IngestResult, Path]:
    today = today or date.today()
    cfg = load_market_config(slug, root / "config")
    priors = load_yaml((root / "config" / "pipeline_priors.yaml")) if (root / "config" / "pipeline_priors.yaml").exists() \
        else load_yaml(CONFIG_DIR / "pipeline_priors.yaml")
    mappings = load_mappings(root / "data" / "mappings")
    inbox = inbox or root / "inbox" / slug
    files = collect_inbox(inbox)
    res = ingest_files(files, cfg, mappings, priors.get("default_open_month", 7), today)
    run_dir = root / "runs" / slug / res.run_id
    report = write_outputs(res, cfg, root / "data" / "processed" / slug, run_dir, today)
    if res.passed and move:
        move_raw(inbox, root / "data" / "raw" / slug, today)
    return res, report
