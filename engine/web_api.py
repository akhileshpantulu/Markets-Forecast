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
from engine.forecast import load_driver_priors, load_inputs
from engine.ledger import registry as ledger_registry
from engine.ledger.report import render as render_ledger
from engine.ledger.run import run_ledger
from engine.settings import settings_schema

# Upload slots on the page. Slots marked enabled=False are shown but not yet accepted.
SLOTS = [
    {"id": "performance", "label": "Market performance", "hint": "CoStar DataTable export, monthly",
     "required": True, "enabled": True},
    {"id": "pipeline", "label": "Pipeline", "hint": "CoStar property export of pipeline hotels",
     "required": True, "enabled": True},
    {"id": "drivers", "label": "Drivers", "hint": "drivers.yaml from /forecast, or your own cards (YAML or JSON)",
     "required": False, "enabled": True, "accept": ".yaml,.yml,.json"},
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


def _json_safe(x):
    """NaN and infinity become null anywhere in the output, so one blank cell in an export cannot break a run."""
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return None if math.isnan(x) or math.isinf(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


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
                   "note": f.note} for f in res.files],
        "checks": {g: [{"id": c.id, "level": c.level, "status": c.status, "title": c.title, "message": c.message,
                        "details": c.details} for c in cs] for g, cs in res.checks.items()},
        "breaks": [b.to_dict() | {"description": b.describe()} for b in res.breaks],
        "validation_md": render_validation(res, cfg, today),
    }
    return out


# Last ingested data, kept in the browser's Python session so changing settings reruns only the forecast.
_STATE: dict = {}
DATA_SLOTS = {"performance", "pipeline", "reference", "optional"}


def parse_drivers_file(data, filename: str) -> list[dict]:
    return parse_research_file(data, filename)[0]


def parse_research_file(data, filename: str) -> tuple[list[dict], list[dict] | None]:
    """A drivers file: a list of cards, or a mapping with 'drivers' and optionally 'coverage' (research output)."""
    raw = _as_bytes(data).decode("utf-8")
    doc = json.loads(raw) if filename.lower().endswith(".json") else yaml.safe_load(raw)
    coverage = None
    if isinstance(doc, dict):
        coverage = doc.get("coverage")
        doc = doc.get("drivers", [])
    if not isinstance(doc, list):
        raise ValueError("A drivers file must be a list of driver cards, or a mapping with a 'drivers' list.")
    return doc, coverage if isinstance(coverage, list) else None


def _coverage(cards: list[dict], coverage: list[dict] | None) -> dict | None:
    if coverage is None:
        return None
    from engine.drivers.doc import check_coverage, load_checklist
    rows, issues = check_coverage(load_checklist(CONFIG_DIR.parent), coverage, {c.get("id") for c in cards})
    return {"rows": rows, "issues": issues, "answered": sum(1 for r in rows if r["status"] != "missing"),
            "items": len({r["id"] for r in rows})}


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]


def _pipeline_context(cfg: MarketConfig, res: IngestResult, pipe_file: dict | None) -> dict:
    """Projects for the map tab and the pipeline export, numbered in opening order."""
    from engine.pipeline_export import sites
    raw = None
    if pipe_file:
        try:
            raw = read_table(pipe_file["data"], filename=pipe_file["filename"])
        except Exception:  # the ingest already reported any problem with the file
            raw = None
    m = res.monthly.sort_values("period_start")
    last = m.iloc[-1]
    inv = last.get("inventory_rooms")
    return {"sites": sites(res.pipeline, raw, cfg.country), "raw": raw,
            "current_supply": None if inv is None or pd.isna(inv) else float(inv),
            "as_of": f"{MONTH_NAMES[last['period_start'].month - 1]} {last['period_start'].year}",
            "first_year": int(last["period_start"].year)}


def export_pipeline(map_png=None) -> str:
    """The uploaded pipeline in the template format, as base64 xlsx. map_png: PNG bytes of the map with pins."""
    import base64
    from engine.pipeline_export import build_workbook
    if "res" not in _STATE:
        raise ValueError("Run the market first.")
    ctx = _STATE["pipeline_ctx"]
    cfg = _STATE["cfg"]
    png = _as_bytes(map_png) if map_png is not None and type(map_png).__name__ != "JsNull" else None
    data = build_workbook(cfg.name, ctx["sites"], ctx["raw"], ctx["current_supply"], ctx["as_of"], ctx["first_year"], png or None)
    return base64.b64encode(data).decode("ascii")


def research_brief(cfg: MarketConfig, res: IngestResult) -> str:
    from engine.drivers.brief import render_brief
    from engine.ledger.registry import TIER_LABEL, tier_of
    last = res.monthly["period_start"].max()
    projects = []
    if res.pipeline is not None:
        for r in res.pipeline.itertuples():
            if r.stage in ("under_construction", "under_renovation", "final_planning", "planning", "proposed", "deferred") and r.rooms >= 50:
                projects.append({"name": r.name, "rooms": float(r.rooms), "stage": r.stage,
                                 "tier": TIER_LABEL.get(tier_of(getattr(r, "chain_scale", None)), "")})
    return render_brief({"name": cfg.name, "slug": cfg.slug, "costar_market": cfg.costar_market, "costar_submarket": cfg.costar_submarket,
                         "country": cfg.country, "currency": cfg.currency, "last_actual_month": last.strftime("%Y-%m"),
                         "horizon": cfg.horizon_years}, projects)


def _forecast(paths: int | None) -> dict:
    st = _STATE
    cfg = st["cfg"]
    _, ppri, macro = load_inputs(cfg)
    drivers = list(st.get("drivers_file") or []) + list(st.get("drivers_extra") or [])
    try:
        fc = run_ledger(st["res"], cfg, ppri, macro, drivers=drivers, settings=st.get("settings"), n_paths=paths or 4000)
        fc["report_md"] = render_ledger(fc, cfg.name)
        fc["coverage"] = _coverage(list(st.get("drivers_file") or []), st.get("coverage"))
        return {"forecast": fc}
    except Exception as exc:  # report, do not hide, a forecast failure
        return {"forecast_error": f"{type(exc).__name__}: {exc}"}


LEGACY_FIELDS = {"pipeline": None, "inflation": None, "drivers": None,
                 "supply": {"supply.removal_rate", "supply.unannounced_enabled", "supply.unannounced_rate"}}


def _schema(cfg: MarketConfig, years: list[int] | None) -> list[dict]:
    """Assumptions tab: the calibration registry, plus pipeline, supply-floor, inflation and driver settings."""
    _, ppri, macro = load_inputs(cfg)
    out = ledger_registry.schema()
    for g in settings_schema(ppri, macro, load_driver_priors(), years):
        if g["id"] not in LEGACY_FIELDS:
            continue
        keep = LEGACY_FIELDS[g["id"]]
        fields = [dict(f, status="user_decision" if g["id"] == "pipeline" else f.get("status", "default"), evidence="")
                  for f in g["fields"] if keep is None or f["key"] in keep]
        if g["id"] == "supply":
            g = {**g, "title": "Supply floor and closures", "help": "Hotel stock never grows below its long-run trend; closures each year."}
        out.insert(1 if g["id"] == "pipeline" else len(out), {**g, "fields": fields})
    return out


def run_ingest(files: list[dict], market: dict, today: str | None = None, forecast: bool = True,
               paths: int | None = None, settings: dict | None = None, drivers: list[dict] | None = None) -> str:
    """files: [{"slot": "performance"|"pipeline"|"drivers"|..., "filename": str, "data": bytes}]. Returns JSON text.

    When validation passes and forecast is True, the forecast runs too, with any drivers and settings.
    """
    day = date.fromisoformat(today) if today else date.today()
    enabled = {s["id"] for s in SLOTS if s["enabled"]}
    inputs = [InputFile(f["slot"], f["filename"], _as_bytes(f["data"])) for f in files
              if f["slot"] in enabled and f["slot"] in DATA_SLOTS]
    cfg = resolve_config(market)
    res = ingest_files(inputs, cfg, load_mappings(), today=day)
    out = result_to_dict(res, cfg, day)
    drivers_file, drivers_error, coverage = [], None, None
    for f in files:
        if f["slot"] == "drivers":
            try:
                drivers_file, coverage = parse_research_file(f["data"], f["filename"])
            except Exception as exc:
                drivers_error = f"{f['filename']}: {exc}"
    _STATE.clear()
    pipe_file = next(({"data": _as_bytes(f["data"]), "filename": f["filename"]} for f in files if f["slot"] == "pipeline"), None)
    _STATE.update({"res": res, "cfg": cfg, "drivers_file": drivers_file, "drivers_extra": drivers or [], "coverage": coverage,
                   "settings": settings or {}, "cache": {}})
    if drivers_error:
        out["drivers_error"] = drivers_error
    if forecast and res.passed:
        out.update(_forecast(paths))
        y0 = int(res.monthly["period_start"].max().year)
        out["settings_schema"] = _schema(cfg, [y0 + i for i in range(1, cfg.horizon_years + 1)])
        try:
            ctx = _pipeline_context(cfg, res, pipe_file)
            _STATE["pipeline_ctx"] = ctx
            out["pipeline_map"] = {"sites": ctx["sites"], "current_supply": ctx["current_supply"], "as_of": ctx["as_of"],
                                   "country": cfg.country}
        except Exception as exc:  # the map and export are conveniences; never block the forecast
            out["pipeline_map_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["research_brief"] = research_brief(cfg, res)
        except Exception as exc:  # the brief is a convenience; never block the forecast
            out["research_brief_error"] = f"{type(exc).__name__}: {exc}"
    return json.dumps(_json_safe(out), allow_nan=False)


def rerun(settings: dict | None, drivers: list[dict] | None, paths: int | None = None, use_file_drivers: bool = True) -> str:
    """Rerun the forecast on the last ingested data with new settings and extra drivers."""
    if "res" not in _STATE:
        return json.dumps({"forecast_error": "No data loaded yet. Run with files first."})
    _STATE["settings"] = settings or {}
    _STATE["drivers_extra"] = drivers or []
    if not use_file_drivers:
        _STATE["drivers_file"] = []
    return json.dumps(_json_safe(_forecast(paths)), allow_nan=False)


def app_info() -> str:
    return json.dumps({"engine_version": __version__, "slots": SLOTS, "markets": list_markets()})


def config_yaml(market: dict) -> str:
    return yaml.safe_dump(resolve_config(market).model_dump(), sort_keys=False)
