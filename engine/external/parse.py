"""Parsers for the external formats. Return tidy pandas frames."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine.config import REPO_ROOT

OUT = REPO_ROOT / "data" / "external"


def _cat_index(dim: dict) -> list[str]:
    idx = dim["category"]["index"]
    if isinstance(idx, list):
        return list(idx)
    return [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]


def parse_jsonstat(obj: dict) -> pd.DataFrame:
    """Eurostat JSON-stat 2.0 dataset to a long frame: one column per dimension, 'value' and 'status'."""
    ids = obj["id"]
    sizes = obj["size"]
    cats = [_cat_index(obj["dimension"][d]) for d in ids]
    vals = obj.get("value", {})
    items = vals.items() if isinstance(vals, dict) else enumerate(vals)
    status = obj.get("status") or {}
    if isinstance(status, list):
        status = {i: s for i, s in enumerate(status) if s}
    status = {int(k): v for k, v in status.items()}
    rows = []
    for k, v in items:
        if v is None:
            continue
        pos = np.unravel_index(int(k), sizes)
        rows.append([cats[i][p] for i, p in enumerate(pos)] + [float(v), status.get(int(k))])
    return pd.DataFrame(rows, columns=list(ids) + ["value", "status"])


def parse_imf(obj: dict) -> pd.DataFrame:
    rows = []
    for ind, by_c in (obj.get("values") or {}).items():
        for country, by_y in by_c.items():
            for y, v in by_y.items():
                if v is not None:
                    rows.append({"indicator": ind, "country": country, "year": int(y), "value": float(v)})
    return pd.DataFrame(rows, columns=["indicator", "country", "year", "value"])


def parse_ecb_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    keep = [c for c in ("CURRENCY", "CURRENCY_DENOM", "TIME_PERIOD", "OBS_VALUE") if c in df.columns]
    return df[keep].rename(columns={"TIME_PERIOD": "period", "OBS_VALUE": "value", "CURRENCY": "currency"})


def parse_ine(obj: list) -> pd.DataFrame:
    rows = []
    for s in obj:
        for d in s.get("Data", []):
            rows.append({"code": s.get("COD"), "name": s.get("Nombre"), "year": d.get("Anyo"),
                         "period": d.get("FK_Periodo"), "value": d.get("Valor")})
    return pd.DataFrame(rows)


PARSERS = {"jsonstat": parse_jsonstat, "imf_datamapper": parse_imf, "json": lambda o: o, "ine_json": parse_ine}


def load(source_id: str, out: Path = OUT) -> tuple[pd.DataFrame | dict | None, dict | None]:
    """(parsed data, provenance meta) or (None, None) when not fetched yet."""
    meta_path = out / f"{source_id}.meta.json"
    if not meta_path.exists():
        return None, None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw = (out / meta["body_file"]).read_text(encoding="utf-8")
    if meta["format"] == "ecb_csv":
        return parse_ecb_csv(raw), meta
    return PARSERS[meta["format"]](json.loads(raw)), meta
