"""In-memory ingestion: bytes in, validated frames and checks out.

No disk access here, so the browser build can call it directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from engine import __version__
from engine.config import MarketConfig
from engine.ingest.aggregate import to_annual
from engine.ingest.mapping import (
    Mapping,
    MappingError,
    apply_performance_mapping,
    apply_pipeline_mapping,
    detect_mapping,
    mapped_and_ignored,
)
from engine.ingest.reader import ReadError, RawTable, read_table
from engine.ingest.validate import Check, validate_performance, validate_pipeline

CATEGORIES = ("performance", "pipeline", "reference", "optional")


@dataclass
class InputFile:
    category: str           # performance | pipeline | reference | optional
    filename: str
    data: bytes


@dataclass
class FileReport:
    category: str
    filename: str
    sha256: str
    mapping: str | None
    sheet: str | None
    rows: int
    snapshot_date: date | None
    mapped: list[tuple[str, str]] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    duplicate_headers: list[str] = field(default_factory=list)
    other_sheets: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class IngestResult:
    market: str
    monthly: pd.DataFrame | None
    annual: pd.DataFrame | None
    pipeline: pd.DataFrame | None
    reference_monthly: pd.DataFrame | None
    reference_annual: pd.DataFrame | None
    checks: dict[str, list[Check]]
    files: list[FileReport]
    notes_text: str | None
    run_id: str
    fatal: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.fatal and not any(c.failed_hard for cs in self.checks.values() for c in cs)


def compute_run_id(files: list[InputFile], mappings: list[Mapping], today: date) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: (x.category, x.filename)):
        h.update(f.category.encode())
        h.update(hashlib.sha256(f.data).digest())
    for m in mappings:
        h.update(m.model_dump_json().encode())
    h.update(__version__.encode())
    return f"{today.isoformat()}-{h.hexdigest()[:7]}"


def _read_and_map(f: InputFile, mappings: list[Mapping], kind: str) -> tuple[RawTable, Mapping, FileReport]:
    raw = read_table(f.data, filename=f.filename)
    mapping = detect_mapping(raw, mappings, kind=kind)
    if mapping.sheet and raw.sheet != mapping.sheet:
        raw = read_table(f.data, filename=f.filename, sheet=mapping.sheet, header_row=mapping.header_row)
    used, ignored = mapped_and_ignored(raw, mapping)
    rep = FileReport(f.category, f.filename, raw.sha256, mapping.path, raw.sheet, len(raw.rows),
                     mapping.snapshot_date(f.filename), used, ignored, raw.duplicate_headers, raw.other_sheets)
    return raw, mapping, rep


def _ingest_performance(files: list[InputFile], mappings, cfg: MarketConfig, label: str, reports, fatal):
    frames, xcs, errs = [], [], []
    for f in files:
        try:
            raw, mapping, rep = _read_and_map(f, mappings, "performance")
        except (MappingError, ReadError) as exc:
            fatal.append(str(exc))
            continue
        reports.append(rep)
        df, xc, e = apply_performance_mapping(raw, mapping, label)
        frames.append(df)
        xcs.append(xc)
        errs += [f"{f.filename} {x}" for x in e]
    if not frames:
        return None, None, []
    raw_df = pd.concat(frames, ignore_index=True)
    xc = pd.concat(xcs, ignore_index=True).drop_duplicates("period_start")
    checks, monthly = validate_performance(raw_df, xc, errs, cfg)
    annual = to_annual(monthly, cfg.fiscal_year_start_month) if not monthly.empty else None
    return monthly, annual, checks


def ingest_files(files: list[InputFile], cfg: MarketConfig, mappings: list[Mapping],
                 default_open_month: int = 7, today: date | None = None) -> IngestResult:
    today = today or date.today()
    reports: list[FileReport] = []
    fatal: list[str] = []
    checks: dict[str, list[Check]] = {}
    by_cat = {c: [f for f in files if f.category == c] for c in CATEGORIES}
    unknown = [f.filename for f in files if f.category not in CATEGORIES]
    if unknown:
        fatal.append(f"files outside the known folders: {unknown}")

    if not by_cat["performance"]:
        fatal.append("no performance export found in performance/")
    monthly, annual, pchecks = _ingest_performance(by_cat["performance"], mappings, cfg, cfg.slug, reports, fatal)
    checks["performance"] = pchecks

    ref_m = ref_a = None
    if by_cat["reference"]:
        ref_m, ref_a, rchecks = _ingest_performance(by_cat["reference"], mappings, cfg, f"{cfg.slug}:reference",
                                                    reports, fatal)
        checks["reference"] = rchecks

    pipeline = None
    if not by_cat["pipeline"]:
        fatal.append("no pipeline export found in pipeline/")
    pframes, perrs, snap = [], [], None
    for f in by_cat["pipeline"]:
        try:
            raw, mapping, rep = _read_and_map(f, mappings, "pipeline")
            reports.append(rep)
            df, e = apply_pipeline_mapping(raw, mapping, default_open_month)
        except (MappingError, ReadError) as exc:
            fatal.append(str(exc))
            continue
        pframes.append(df)
        perrs += [f"{f.filename} {x}" for x in e]
        snap = max(filter(None, [snap, rep.snapshot_date]), default=None)
    if pframes:
        allp = pd.concat(pframes, ignore_index=True)
        # Historical snapshots: keep all; the latest snapshot is the current pipeline.
        if allp["snapshot_date"].nunique() > 1:
            latest = allp["snapshot_date"].max()
            pipeline = allp
            current = allp[allp["snapshot_date"] == latest]
        else:
            pipeline = current = allp
        checks["pipeline"] = validate_pipeline(current, perrs, monthly, cfg, snap)

    notes_text = None
    for f in by_cat["optional"]:
        if f.filename.lower() == "notes.txt":
            notes_text = f.data.decode("utf-8", errors="replace")
        reports.append(FileReport(f.category, f.filename, hashlib.sha256(f.data).hexdigest(), None, None, 0, None,
                                  note="received; mapped in a later phase" if f.filename.lower() != "notes.txt"
                                  else "user notes"))

    run_id = compute_run_id(files, mappings, today)
    return IngestResult(cfg.slug, monthly, annual, pipeline, ref_m, ref_a, checks, reports, notes_text, run_id, fatal)
