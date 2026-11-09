"""Turn driver cards into per-path effects for the simulation.

Each card is drawn once per path: it happens with its probability, and if it
happens its size is drawn from its magnitude distribution. Effects are levels
(log), so a one-off event lifts its year and the next year grows off a normal
base again (the payback). Each card has its own random stream, so adding or
removing one card never changes the draws of the baseline or of other cards.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np

from engine.drivers.schema import DriverCard
from engine.drivers.validate import resolve_probability


@dataclass
class DriverEffects:
    demand: np.ndarray          # [paths, years] log level, years = Y0..Yn
    adr: np.ndarray
    supply: np.ndarray
    shock_mult: np.ndarray      # [paths, years-1] for Y1..Yn
    applied: list[dict] = field(default_factory=list)

    def as_levels(self) -> dict:
        return {"demand": self.demand, "adr": self.adr, "supply": self.supply, "shock_mult": self.shock_mult}


def profile(card: DriverCard, years: list[int]) -> np.ndarray:
    """Share of the full effect in each year (0..1)."""
    t = card.timing
    start = t.start_year
    peak = t.peak_year if t.peak_year is not None else start
    decay = t.decay_years or 0
    shape = card.resolved_shape()
    out = np.zeros(len(years))
    for i, y in enumerate(years):
        if y < start:
            continue
        if shape == "one_off":
            out[i] = 1.0 if y == peak else 0.0
        elif y <= peak:
            out[i] = (y - start + 1) / (peak - start + 1)
        elif shape == "step":
            out[i] = 1.0
        else:
            k = y - peak
            out[i] = max(0.0, 1 - k / (decay + 1))
    return out


def _stub_factor(card: DriverCard, stub_year: int, cutoff_month: int) -> float:
    """Stub year: effects apply to the simulated months only. If the event months are known and
    all already observed, the effect is in the actuals; if all are still to come, the year's effect
    is concentrated in the remaining months."""
    if card.timing.months is None:
        return 1.0
    rest = [m for m in card.timing.months if m > cutoff_month]
    if not rest:
        return 0.0
    rest_share = (12 - cutoff_month) / 12
    return (len(rest) / len(card.timing.months)) / rest_share


def build_effects(cards: list[DriverCard], years: list[int], n_paths: int, seed: int, base_rooms: float,
                  ladder: dict, cutoff_month: int, prob_override: dict[str, float] | None = None,
                  shock_cap: float = 5.0) -> DriverEffects:
    n_y = len(years)
    eff = DriverEffects(np.zeros((n_paths, n_y)), np.zeros((n_paths, n_y)), np.zeros((n_paths, n_y)),
                        np.ones((n_paths, n_y - 1)))
    for c in cards:
        if c.magnitude is None:          # calculator cards are sized by the ledger engine
            continue
        p = (prob_override or {}).get(c.id)
        if p is None:
            p = resolve_probability(c, ladder)
        if p is None:
            continue
        rng = np.random.default_rng([seed, zlib.crc32(c.id.encode())])
        occurs = rng.random(n_paths) < p
        m = c.magnitude
        if m.dist == "fixed" or m.high <= m.low:
            size = np.full(n_paths, m.mode)
        else:
            size = rng.triangular(m.low, m.mode, m.high, n_paths)
        prof = profile(c, years)
        if prof[0] > 0:
            prof = prof.copy()
            prof[0] *= _stub_factor(c, years[0], cutoff_month)
        if c.affects == "shock_probability":
            mult = np.minimum(1 + (size - 1)[:, None] * prof[None, 1:], shock_cap)
            eff.shock_mult *= np.where(occurs[:, None], mult, 1.0)
        else:
            if m.unit == "rooms":
                level = np.log1p(size / base_rooms)
            else:
                level = np.log1p(size / 100.0)
            add = np.where(occurs, level, 0.0)[:, None] * prof[None, :]
            if c.affects == "supply":
                add[:, 0] = 0.0                             # stub-year supply comes from actuals and the pipeline
            getattr(eff, c.affects)[:] += add
        eff.applied.append({"id": c.id, "title": c.title, "affects": c.affects, "probability": float(p),
                            "occurrence_rate": float(occurs.mean()), "profile": {str(y): float(v) for y, v in zip(years, prof)},
                            "mean_size_if_occurs": float(size.mean()), "unit": m.unit})
    return eff
