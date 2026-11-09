"""Configuration models and loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
MAPPINGS_DIR = REPO_ROOT / "data" / "mappings"


def load_yaml(path: Path | str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: int = Field(20000, gt=0)
    export_paths: int = Field(2000, gt=0)
    seed: int


class SeriesBreak(BaseModel):
    """A user decision about a detected level break (validation check P18)."""
    model_config = ConfigDict(extra="forbid")
    measure: Literal["adr", "demand", "supply", "occupancy"]
    year: int                      # growth from year-1 to year is affected
    action: Literal["exclude_growth", "keep"]
    decided_by: str = "pending"
    note: str = ""


class EstimationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    covid_years: list[int] = [2020, 2021, 2022]
    min_complete_years: int = 8
    series_breaks: list[SeriesBreak] = []


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closure_flag_ratio: float = 0.90
    outlier_robust_z: float = 4.0


class MarketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    geography_level: Literal["market", "submarket", "tract", "country", "region"]
    costar_market: str | None = None
    costar_submarket: str | None = None
    country: str
    country_iso: str
    currency: str
    year_basis: Literal["calendar", "fiscal"] = "calendar"
    fiscal_year_start_month: int = Field(1, ge=1, le=12)
    horizon_years: int = Field(5, ge=1, le=15)
    stub_year: int | Literal["auto"] = "auto"
    simulation: SimulationConfig
    estimation: EstimationConfig = EstimationConfig()
    ingest: IngestConfig = IngestConfig()
    reference_series: str | None = None
    airport: str | None = None
    events: str | None = None
    # Tier whose rate is closest to the market's average (supply-quality lines). An assumption until a
    # class-level CoStar export is loaded: luxury, upper_upscale, upscale, upper_midscale, midscale, economy.
    average_tier: str | None = None


def load_market_config(slug: str, config_dir: Path | None = None) -> MarketConfig:
    path = (config_dir or CONFIG_DIR) / "markets" / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No market config at {path}")
    return MarketConfig.model_validate(load_yaml(path))
