"""Read the private European performance panel (repository akhileshpantulu/marketsdata).

The panel is licensed CoStar data. It lives only in the private data repository and is never
copied into this (public) repository or the web build. Attach it in a session with add_repo and
clone it to /home/user/marketsdata, or set MARKETS_DATA_DIR.

load_panel("monthly" | "annual")  -> the whole panel
submarket_monthly(slug)           -> one submarket in the engine's internal monthly schema
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

DEFAULT_DIR = Path("/home/user/marketsdata")


def data_dir(path: str | Path | None = None) -> Path:
    d = Path(path or os.environ.get("MARKETS_DATA_DIR") or DEFAULT_DIR)
    if not (d / "panel").is_dir():
        raise FileNotFoundError(f"No panel at {d / 'panel'}. Attach akhileshpantulu/marketsdata (private) and clone it there, "
                                "or set MARKETS_DATA_DIR.")
    return d


def load_panel(kind: str = "monthly", path: str | Path | None = None) -> pd.DataFrame:
    return pd.read_parquet(data_dir(path) / "panel" / f"performance_{kind}.parquet")


def geographies(path: str | Path | None = None) -> pd.DataFrame:
    return pd.read_csv(data_dir(path) / "panel" / "geographies.csv")


def to_internal(rows: pd.DataFrame) -> pd.DataFrame:
    """Panel rows for one geography and slice -> the internal monthly schema (actual months with performance)."""
    g = rows[rows["is_actual"] & rows["supply"].notna()].sort_values("period").copy()
    out = pd.DataFrame({
        "market": g["slug"], "period_start": g["period"], "period_type": "month",
        "supply": g["supply"], "demand": g["demand"], "occupancy": g["demand"] / g["supply"],
        "adr": g["revenue"] / g["demand"], "revpar": g["revenue"] / g["supply"], "revenue": g["revenue"],
        "source_file": "marketsdata:" + g["source_file"].astype(str)})
    for c in ("inventory_rooms", "existing_buildings", "under_construction_rooms", "under_construction_buildings"):
        out[c] = g[c] if c in g else float("nan")
    out["days_in_period"] = out["period_start"].dt.days_in_month
    out["available_rooms"] = out["supply"] / out["days_in_period"]
    out["closed_rooms"] = (out["inventory_rooms"] - out["available_rooms"]).clip(lower=0)
    return out.reset_index(drop=True)


def submarket_monthly(slug: str, slice_: str = "All", path: str | Path | None = None) -> pd.DataFrame:
    p = load_panel("monthly", path)
    rows = p[(p["slug"] == slug) & (p["slice"] == slice_)]
    if rows.empty:
        raise KeyError(f"{slug!r} ({slice_}) is not in the panel; see panel/CATALOG.md for slugs.")
    return to_internal(rows)
