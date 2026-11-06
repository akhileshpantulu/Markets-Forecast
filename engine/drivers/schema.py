"""Driver card schema (BUILD_PROMPT section 6.6, plus the shock_probability extension the user
approved on 2026-11-06)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["air_access", "supply", "events", "macro", "fx", "geopolitical", "policy", "corporate", "mice",
                   "leisure_sentiment", "regulation", "infrastructure", "other"]
EventStatus = Literal["rumour", "credible_report", "announced", "tickets_on_sale", "held", "cancelled"]


class Mechanism(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int = Field(ge=1, le=5)
    effect: str
    sign: Literal["+", "-"]


class Timing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_year: int
    peak_year: int | None = None
    decay_years: int | None = None
    months: list[int] | None = None          # calendar months the effect falls in, when known

    @field_validator("months")
    @classmethod
    def _months(cls, v):
        if v is not None and any(m < 1 or m > 12 for m in v):
            raise ValueError("months must be 1..12")
        return v


class Magnitude(BaseModel):
    """Size if the driver happens.

    pct_of_level / pct_points_of_growth: percent of the year's demand or ADR level (0.8 means +0.8%).
    rooms: rooms added to supply (supply drivers). multiplier: factor on the annual shock probability.
    """
    model_config = ConfigDict(extra="forbid")
    dist: Literal["triangular", "fixed"] = "triangular"
    low: float
    mode: float
    high: float
    unit: Literal["pct_of_level", "pct_points_of_growth", "rooms", "multiplier"]

    @field_validator("high")
    @classmethod
    def _order(cls, v, info):
        lo, mo = info.data.get("low"), info.data.get("mode")
        if lo is not None and mo is not None and not (lo <= mo <= v):
            raise ValueError("magnitude must satisfy low <= mode <= high")
        return v


class Analog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str
    market: str
    measured_effect: str
    source: str
    measured_magnitude: float | None = None   # same unit as the card's magnitude, when it can be stated


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    url: str
    publisher: str
    published: str
    accessed: str


class DriverCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    category: Category
    affects: Literal["demand", "adr", "supply", "shock_probability"]
    segment: Literal["leisure", "corporate", "group", "transit", "all"] = "all"
    mechanism_chain: list[Mechanism] = []
    timing: Timing
    shape: Literal["one_off", "temporary", "step"] | None = None
    probability: float | None = Field(None, ge=0, le=1)
    event_status: EventStatus | None = None
    probability_note: str = ""
    magnitude: Magnitude
    magnitude_note: str = ""
    conditional_on: list[str] = []
    already_in_baseline: Literal["none", "partial", "full"]
    baseline_overlap_note: str = ""
    counter_effects: list[str] = []
    analogs: list[Analog] = []
    evidence: list[Evidence] = []
    confidence: Literal["low", "medium", "high"] = "low"
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    reviewer_note: str = ""
    origin: Literal["research", "user"] = "research"

    def resolved_shape(self) -> str:
        if self.shape:
            return self.shape
        if self.affects == "supply":
            return "step"
        return "temporary" if (self.timing.decay_years or 0) > 0 else "one_off"
