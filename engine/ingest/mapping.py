"""Source-column mappings: load, detect, apply.

A mapping file in data/mappings/ declares which source columns feed which
internal fields. Detection is by header signature. Any ambiguity (no mapping,
more than one mapping, a mapped column missing or duplicated, an unknown stage
label) raises an error that stops ingestion so the user can be asked.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from engine.config import MAPPINGS_DIR, load_yaml
from engine.ingest.periods import (
    PeriodParseError,
    month_number,
    parse_month_period,
    parse_month_year_string,
    parse_year_period,
)
from engine.ingest.reader import RawTable


class MappingError(ValueError):
    """Hard failure: data cannot be mapped without user input."""


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    type: Literal["float", "int", "str"]
    unit: str | None = None


class PeriodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    format: Literal["month_name_year", "year"]


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    kind: Literal["performance", "pipeline"]
    period_type: Literal["month", "year"] | None = None
    sheet: str | None = None
    header_row: int = 1
    signature: list[str]
    null_tokens: list[str] = ["-", ""]
    snapshot_date_from_filename: str | None = None
    period: PeriodSpec | None = None
    fields: dict[str, FieldSpec]
    crosscheck: dict[str, dict[str, str]] = {}
    stage_map: dict[str, str] = {}
    projected_open: dict[str, dict[str, str]] = {}
    path: str | None = None

    def snapshot_date(self, filename: str) -> date | None:
        if not self.snapshot_date_from_filename:
            return None
        m = re.search(self.snapshot_date_from_filename, filename)
        return date.fromisoformat(m.group(1)) if m else None


def load_mappings(directory: Path | None = None) -> list[Mapping]:
    directory = directory or MAPPINGS_DIR
    out = []
    for p in sorted(Path(directory).glob("*.yaml")):
        m = Mapping.model_validate(load_yaml(p))
        m.path = str(p.name)
        out.append(m)
    return out


def detect_mapping(raw: RawTable, mappings: list[Mapping], kind: str | None = None) -> Mapping:
    headers = set(raw.headers)
    candidates = [m for m in mappings if set(m.signature) <= headers and (kind is None or m.kind == kind)]
    if not candidates:
        raise MappingError(
            f"{raw.filename}: no mapping matches its headers. A new mapping in data/mappings/ is needed. "
            f"Headers: {raw.headers}"
        )
    if len(candidates) > 1:
        raise MappingError(
            f"{raw.filename}: ambiguous, matches several mappings: {[m.source for m in candidates]}"
        )
    mapping = candidates[0]
    _check_columns(raw, mapping)
    return mapping


def _check_columns(raw: RawTable, mapping: Mapping) -> None:
    idx = raw.header_index()
    needed = [f.column for f in mapping.fields.values()]
    if mapping.period:
        needed.append(mapping.period.column)
    for group in mapping.crosscheck.values():
        needed.extend(group.values())
    missing = [c for c in needed if c not in idx]
    dup = [c for c in needed if len(idx.get(c, [])) > 1]
    if missing:
        raise MappingError(f"{raw.filename}: mapped columns missing: {missing}")
    if dup:
        raise MappingError(f"{raw.filename}: mapped columns appear more than once: {dup}")


def mapped_and_ignored(raw: RawTable, mapping: Mapping) -> tuple[list[tuple[str, str]], list[str]]:
    used: list[tuple[str, str]] = []
    if mapping.period:
        used.append((mapping.period.column, "period_start"))
    used += [(f.column, name) for name, f in mapping.fields.items()]
    for group, cols in mapping.crosscheck.items():
        used += [(col, f"crosscheck:{group}:{name}") for name, col in cols.items()]
    used_cols = {c for c, _ in used}
    ignored = [h for h in dict.fromkeys(raw.headers) if h and h not in used_cols]
    return used, ignored


def _convert(value, ftype: str, null_tokens: set[str], where: str, errors: list[str]):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s in null_tokens:
            return None
        value = s
    try:
        if ftype == "float":
            return float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
        if ftype == "int":
            f = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
            if f != int(f):
                errors.append(f"{where}: expected integer, got {value!r}")
                return None
            return int(f)
        return str(value).strip()
    except (TypeError, ValueError):
        errors.append(f"{where}: cannot convert {value!r} to {ftype}")
        return None


def _column(raw: RawTable, name: str) -> list:
    i = raw.header_index()[name][0]
    return [r[i] if i < len(r) else None for r in raw.rows]


def apply_performance_mapping(raw: RawTable, mapping: Mapping, market: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Return (performance frame, crosscheck frame, conversion errors)."""
    nulls = set(mapping.null_tokens)
    errors: list[str] = []
    periods = []
    for i, v in enumerate(_column(raw, mapping.period.column)):
        try:
            if mapping.period.format == "month_name_year":
                periods.append(parse_month_period(v))
            else:
                periods.append(parse_year_period(v))
        except PeriodParseError as exc:
            errors.append(f"row {i + 2}: {exc}")
            periods.append(pd.NaT)
    data = {"period_start": periods}
    for name, spec in mapping.fields.items():
        data[name] = [
            _convert(v, spec.type, nulls, f"row {i + 2} {spec.column}", errors)
            for i, v in enumerate(_column(raw, spec.column))
        ]
    df = pd.DataFrame(data)
    for name, spec in mapping.fields.items():
        if spec.type in ("float", "int"):
            df[name] = pd.to_numeric(df[name], errors="coerce").astype("float64")
    df.insert(0, "market", market)
    df.insert(2, "period_type", mapping.period_type or "month")
    df["source_file"] = raw.filename

    xc = {"period_start": periods}
    for group, cols in mapping.crosscheck.items():
        for name, col in cols.items():
            xc[f"{group}:{name}"] = [
                _convert(v, "float", nulls, f"row {i + 2} {col}", errors) for i, v in enumerate(_column(raw, col))
            ]
    xdf = pd.DataFrame(xc)
    for c in xdf.columns[1:]:
        xdf[c] = pd.to_numeric(xdf[c], errors="coerce").astype("float64")
    return df, xdf, errors


def apply_pipeline_mapping(raw: RawTable, mapping: Mapping, default_open_month: int = 7) -> tuple[pd.DataFrame, list[str]]:
    nulls = set(mapping.null_tokens)
    errors: list[str] = []
    cols = {}
    for name, spec in mapping.fields.items():
        cols[name] = [
            _convert(v, spec.type, nulls, f"row {i + 2} {spec.column}", errors)
            for i, v in enumerate(_column(raw, spec.column))
        ]
    n = len(raw.rows)
    records = []
    snapshot = mapping.snapshot_date(raw.filename)
    for i in range(n):
        r = {k: v[i] for k, v in cols.items()}
        stage_raw = r.get("stage_raw")
        if stage_raw not in mapping.stage_map:
            raise MappingError(
                f"{raw.filename} row {i + 2}: construction status {stage_raw!r} is not in stage_map of "
                f"{mapping.path}. Ask the user how to treat it."
            )
        stage = mapping.stage_map[stage_raw]
        is_reno = stage == "under_renovation"
        temp_closed = (r.get("operation_status") or "").lower() == "temporarily closed"
        if r.get("expansion_rooms"):
            project_type = "expansion"
        elif is_reno:
            project_type = "renovation_reopening" if temp_closed else "renovation_open"
        else:
            project_type = "new_build"
        spec = mapping.projected_open.get("renovation" if is_reno else "new_build", {})
        year = r.get(spec.get("year")) if spec else None
        month_label = r.get(spec.get("month")) if spec else None
        basis = "missing"
        projected = None
        if year:
            try:
                month = month_number(month_label)
            except Exception as exc:
                errors.append(f"row {i + 2}: {exc}")
                month = None
            if month is None:
                month = default_open_month
                basis = "year_only_default_month"
            else:
                basis = "year_month"
            projected = pd.Timestamp(year=int(year), month=month, day=1)
        constr_start = None
        try:
            constr_start = parse_month_year_string(r.get("constr_start"))
        except PeriodParseError:
            errors.append(f"row {i + 2}: unparseable Constr Start {r.get('constr_start')!r}")
        records.append({
            "project_id": r.get("project_id"),
            "name": r.get("name"),
            "rooms": r.get("rooms"),
            "stage": stage,
            "projected_open": projected,
            "brand": r.get("brand"),
            "chain_scale": r.get("chain_scale"),
            "source_file": raw.filename,
            "snapshot_date": pd.Timestamp(snapshot) if snapshot else pd.NaT,
            "stage_raw": stage_raw,
            "project_type": project_type,
            "in_current_supply": bool(project_type in ("renovation_open", "expansion")),
            "projected_open_basis": basis,
            "operation_status": r.get("operation_status"),
            "scale": r.get("scale"),
            "market": r.get("market"),
            "submarket": r.get("submarket"),
            "constr_start": constr_start,
            "expansion_rooms": r.get("expansion_rooms"),
        })
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["projected_open"] = pd.to_datetime(df["projected_open"])
        df["constr_start"] = pd.to_datetime(df["constr_start"])
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df["rooms"] = pd.to_numeric(df["rooms"]).astype("Int64")
        df["expansion_rooms"] = pd.to_numeric(df["expansion_rooms"]).astype("Int64")
    return df, errors
