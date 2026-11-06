"""Entry point for the browser build (Pyodide).

The page passes dropped files as bytes and gets back one JSON-ready dict. The
same function runs under pytest, so what the page shows is what the Python
engine computes.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date

import numpy as np
import pandas as pd
import yaml

from engine import __version__
from engine.config import CONFIG_DIR, MarketConfig, load_yaml
from engine.ingest.core import IngestResult, InputFile, ingest_files
from engine.ingest.mapping import load_mappings
from engine.ingest.reader import read_table
from engine.ingest.report import render_validation
from engine.forecast import load_inputs, run_forecast
from engine.outputs.markdown import render_backtest, render_baseline, render_history

# Upload slots on the page. Slots marked enabled=False are shown but not yet accepted.
SLOTS = [
    {"id": "performance", "label": "Market performance", "hint": "CoStar DataTable export, monthly",
     "required": True, "enabled": True},
    {"id": "pipeline", "label": "Pipeline", "hint": "CoStar property export of pipeline hotels",
     "required": True, "enabled": True},
    {"id": "airport", "label": "Airline and airport data", "hint": "Format to be added once an example is provided",
     "required": False, "enabled": False},
    {"id": "reference", "label": "Country performance", "hint": "Format to be confirmed once an example is provided",
     "required": False, "enabled": False},
]


def list_markets() -> list[dict]:
    out = []
    for p in sorted((CONFIG_DIR / "markets").glob("*.yaml")):
        c = MarketConfig.model_validate(load_yaml(p))
        out.append({"slug": c.slug, "name": c.name, "currency": c.currency, "costar_market": c.costar_market,
                    "costar_submarket": c.costar_submarket})
    return out


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "market"


def _as_bytes(x) -> bytes:
    """Accept bytes, memoryview, or a Pyodide JsProxy of a Uint8Array."""
    if hasattr(x, "to_bytes") and not isinstance(x, (bytes, bytearray, memoryview, int)):
        return x.to_bytes()
    return bytes(x)


def peek_json(data, filename: str) -> str:
    return json.dumps(peek_pipeline_geography(_as_bytes(data), filename))


def peek_pipeline_geography(data: bytes, filename: str) -> dict:
    """Read Market and Submarket from a pipeline export to suggest a market config."""
    t = read_table(data, filename=filename)
    out = {}
    for key in ("Market", "Submarket", "Country"):
        if key in t.headers:
            i = t.headers.index(key)
            vals = sorted({str(r[i]).strip() for r in t.rows if i < len(r) and r[i] not in (None, "", "-")})
            out[key.lower()] = vals
    return out


def make_adhoc_config(name: str, currency: str, costar_market: str | None, costar_submarket: str | None,
                      country: str = "", country_iso: str = "XX", seed: int = 20261106) -> MarketConfig:
    geo = "submarket" if costar_submarket else "market"
    return MarketConfig.model_validate({
        "slug": _slugify(name), "name": name, "geography_level": geo, "costar_market": costar_market,
        "costar_submarket": costar_submarket, "country": country or "Unknown", "country_iso": country_iso,
        "currency": currency, "simulation": {"seed": seed},
    })


def resolve_config(market: dict) -> MarketConfig:
    """market = {"slug": ...} for a configured market, or {"adhoc": {...}} for a new one."""
    if market.get("slug"):
        return MarketConfig.model_validate(load_yaml(CONFIG_DIR / "markets" / f"{market['slug']}.yaml"))
    a = market["adhoc"]
    return make_adhoc_config(a["name"], a.get("currency", "EUR"), a.get("costar_market"), a.get("costar_submarket"),
                             a.get("country", ""))


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, date)):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else v.isoformat()
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else float(v)
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _records(df: pd.DataFrame | None, cols: list[str] | None = None) -> list[dict]:
    if df is None:
        return []
    d = df[cols] if cols else df
    return [{k: _clean(v) for k, v in r.items()} for r in d.to_dict(orient="records")]


def result_to_dict(res: IngestResult, cfg: MarketConfig, today: date) -> dict:
    monthly = res.monthly if res.monthly is not None else pd.DataFrame()
    last = monthly["period_start"].max() if not monthly.empty else None
    out = {
        "meta": {
            "run_id": res.run_id, "engine_version": __version__, "market": cfg.name, "slug": cfg.slug,
            "currency": cfg.currency, "passed": res.passed, "date": today.isoformat(),
            "first_month": None if monthly.empty else monthly["period_start"].min().strftime("%Y-%m"),
            "last_month": None if last is None else last.strftime("%Y-%m"),
            "stub_year": None if last is None else int(last.year),
            "stub_actual_months": None if last is None else int((monthly["period_start"].dt.year == last.year).sum()),
        },
        "fatal": res.fatal,
        "files": [{"category": f.category, "filename": f.filename, "mapping": f.mapping, "sheet": f.sheet,
                   "rows": f.rows, "snapshot_date": f.snapshot_date.isoformat() if f.snapshot_date else None,
                   "mapped": [list(m) for m in f.mapped], "ignored": f.ignored,
                   "duplicate_headers": f.duplicate_headers, "note": f.note} for f in res.files],
        "checks": {g: [{"id": c.id, "level": c.level, "status": c.status, "title": c.title, "message": c.message,
                        "details": c.details} for c in cs] for g, cs in res.checks.items()},
        "annual": _records(res.annual, ["year", "months_observed", "complete", "supply", "demand", "occupancy", "adr",
                                        "revpar", "revenue"]) if res.annual is not None else [],
        "monthly": _records(monthly.assign(period=monthly["period_start"].dt.strftime("%Y-%m")),
                            ["period", "supply", "demand", "occupancy", "adr", "revpar", "inventory_rooms",
                             "available_rooms"]) if not monthly.empty else [],
        "pipeline": _records(res.pipeline, ["project_id", "name", "rooms", "stage", "project_type", "projected_open",
                                            "projected_open_basis", "chain_scale", "brand", "operation_status"])
        if res.pipeline is not None else [],
        "breaks": [b.to_dict() | {"description": b.describe()} for b in res.breaks],
        "growth_sample": _records(res.growth_sample) if res.growth_sample is not None else [],
        "influence": {m: {"flagged": inf.flagged, "years": inf.flagged_years()} for m, inf in res.influence.items()},
        "validation_md": render_validation(res, cfg, today),
    }
    return out


def run_ingest(files: list[dict], market: dict, today: str | None = None, forecast: bool = True,
               paths: int | None = None) -> str:
    """files: [{"slot": "performance"|"pipeline"|..., "filename": str, "data": bytes}]. Returns JSON text.

    When validation passes and forecast is True, the baseline forecast (Phases B and C) runs too.
    """
    day = date.fromisoformat(today) if today else date.today()
    enabled = {s["id"] for s in SLOTS if s["enabled"]}
    inputs = [InputFile(f["slot"], f["filename"], _as_bytes(f["data"])) for f in files if f["slot"] in enabled]
    cfg = resolve_config(market)
    res = ingest_files(inputs, cfg, load_mappings(), today=day)
    out = result_to_dict(res, cfg, day)
    if forecast and res.passed:
        try:
            priors, ppri, macro = load_inputs(cfg)
            fc = run_forecast(res, cfg, priors, ppri, macro, n_paths=paths)
            fc["reports"] = {"history_md": render_history(fc, cfg), "backtest_md": render_backtest(fc),
                             "baseline_md": render_baseline(fc, cfg)}
            out["forecast"] = fc
        except Exception as exc:  # report, do not hide, a forecast failure
            out["forecast_error"] = f"{type(exc).__name__}: {exc}"
    return json.dumps(out, allow_nan=False)


def app_info() -> str:
    return json.dumps({"engine_version": __version__, "slots": SLOTS, "markets": list_markets()})


def config_yaml(market: dict) -> str:
    return yaml.safe_dump(resolve_config(market).model_dump(), sort_keys=False)
