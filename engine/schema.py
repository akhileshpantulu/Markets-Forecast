"""Internal data schema.

Performance (minimum): market, period_start, period_type, supply, demand,
occupancy, adr, revpar, revenue, source_file. Extra columns carry inventory and
derived fields.

Pipeline: project_id, name, rooms, stage, projected_open, brand, chain_scale,
source_file, snapshot_date, plus fields needed to model reopenings.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

PERFORMANCE_CORE = [
    "market",
    "period_start",
    "period_type",
    "supply",
    "demand",
    "occupancy",
    "adr",
    "revpar",
    "revenue",
    "source_file",
]

PERFORMANCE_EXTRA = [
    "inventory_rooms",
    "existing_buildings",
    "under_construction_rooms",
    "under_construction_buildings",
    "delivered_rooms_12m",
    "opened_rooms_12m",
    "days_in_period",
    "available_rooms",
    "closed_rooms",
]

PIPELINE_CORE = [
    "project_id",
    "name",
    "rooms",
    "stage",
    "projected_open",
    "brand",
    "chain_scale",
    "source_file",
    "snapshot_date",
]

PIPELINE_EXTRA = [
    "stage_raw",
    "project_type",
    "in_current_supply",
    "projected_open_basis",
    "operation_status",
    "scale",
    "market",
    "submarket",
    "constr_start",
    "expansion_rooms",
]

Stage = Literal[
    "under_construction",
    "under_renovation",
    "final_planning",
    "planning",
    "proposed",
    "deferred",
    "abandoned",
    "existing",
    "demolished",
]

ProjectType = Literal["new_build", "renovation_reopening", "renovation_open", "expansion"]


class PerformanceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    market: str
    period_start: date
    period_type: Literal["month", "year"]
    supply: float = Field(ge=0)
    demand: float = Field(ge=0)
    occupancy: float = Field(ge=0, le=1)
    adr: float = Field(ge=0)
    revpar: float = Field(ge=0)
    revenue: float = Field(ge=0)
    source_file: str


class PipelineRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str
    name: str
    rooms: int = Field(gt=0)
    stage: Stage
    projected_open: date | None
    brand: str | None
    chain_scale: str | None
    source_file: str
    snapshot_date: date | None
    project_type: ProjectType
    in_current_supply: bool


def _to_py(value):
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


def validate_frame(df: pd.DataFrame, model: type[BaseModel], core: list[str]) -> list[str]:
    """Validate each row against a pydantic model. Returns error messages."""
    missing = [c for c in core if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    errors: list[str] = []
    for i, row in enumerate(df.to_dict(orient="records")):
        try:
            model.model_validate({k: _to_py(v) for k, v in row.items()})
        except Exception as exc:  # pydantic.ValidationError
            errors.append(f"row {i}: {exc}")
    return errors
