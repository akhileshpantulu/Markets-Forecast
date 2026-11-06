"""Guardrails against data anomalies such as series level breaks.

A level break is a one-off step in a series that persists: every month for
twelve consecutive months shows a large year-over-year move in the same
direction, the following twelve months do not reverse it, and the preceding
twelve months were not an opposite move (which would make it a recovery). Real shocks
(2001, 2009, 2020) look different: they are uneven across months and they
reverse. A persistent uniform step usually means a change in the CoStar sample,
a geography redefinition, or a unit change.

Breaks are handled two ways:
  - growth observations that contain the step are excluded from parameter
    estimation (engine/history/sample.py);
  - for level statistics (historical ranges, maxima), values before the break
    are rescaled by the step factor ("spliced") so they are comparable with
    values after it.
User overrides live in estimation.series_breaks in the market config.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from engine.config import MarketConfig

BREAK_MEASURES = ("adr", "demand", "supply")
STEP_THRESHOLD = 0.25        # median relative YoY change across the 12-month window
MIN_SAME_SIGN = 11           # of 12 months
REVERSAL_SHARE = 0.5         # following-window move opposite in sign and at least this share of the step
MATCH_MONTHS = 2             # tolerance when matching a break to a config override or a reference break


@dataclass
class LevelBreak:
    measure: str
    start: pd.Timestamp                 # first month whose YoY change contains the step
    step: float                         # relative step, exp(median log YoY) - 1
    same_sign: int                      # months of 12 moving with the step
    min_yoy: float
    max_yoy: float
    following_median: float | None      # median relative YoY of the next 12 months
    confirmed: bool                     # True when the following 12 months exist and do not reverse
    affected_years: list[int] = field(default_factory=list)
    action: str = "exclude_growth"      # exclude_growth | keep | review
    decided_by: str = "auto"
    in_reference: bool | None = None
    note: str = ""

    @property
    def factor(self) -> float:
        return 1.0 + self.step

    def describe(self) -> str:
        follow = "n/a" if self.following_median is None else f"{self.following_median:+.1%}"
        ref = "" if self.in_reference is None else (
            "; same step in reference series" if self.in_reference else "; not in reference series")
        return (f"{self.measure} step of {self.step:+.1%} starting {self.start:%Y-%m} "
                f"({self.same_sign}/12 months same direction, range {self.min_yoy:+.1%} to {self.max_yoy:+.1%}; "
                f"next 12 months median {follow}){ref}. Affected annual growth: "
                f"{', '.join(str(y) for y in self.affected_years)}. Action: {self.action} ({self.decided_by}).")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.strftime("%Y-%m")
        return d


def _log_yoy(monthly: pd.DataFrame, measure: str) -> pd.Series:
    s = monthly.set_index("period_start")[measure].astype(float)
    prior = s.copy()
    prior.index = prior.index + pd.DateOffset(years=1)
    ratio = s / prior.reindex(s.index)
    return np.log(ratio.where(ratio > 0)).replace([np.inf, -np.inf], np.nan)


def _excluded_by_covid(start: pd.Timestamp, covid_years: set[int]) -> bool:
    window = pd.date_range(start, periods=12, freq="MS")
    # A window that touches the COVID years, or the year after them (whose YoY compares with them).
    return any(m.year in covid_years or (m.year - 1) in covid_years for m in window)


def detect_level_breaks(monthly: pd.DataFrame, cfg: MarketConfig,
                        measures: tuple[str, ...] = BREAK_MEASURES) -> list[LevelBreak]:
    covid = set(cfg.estimation.covid_years)
    breaks: list[LevelBreak] = []
    for measure in measures:
        if measure not in monthly.columns:
            continue
        ly = _log_yoy(monthly, measure)
        idx = ly.index
        candidates = []
        for i in range(len(idx) - 11):
            w = ly.iloc[i:i + 12]
            if w.isna().any() or (w.index[-1] - w.index[0]).days > 340:
                continue
            if _excluded_by_covid(idx[i], covid):
                continue
            med = float(w.median())
            if abs(np.expm1(med)) <= STEP_THRESHOLD:
                continue
            same = int((np.sign(w) == np.sign(med)).sum())
            if same < MIN_SAME_SIGN:
                continue
            candidates.append((i, med, same, float(w.mean())))
        # Consecutive qualifying starts describe the same break; keep the start with the largest mean move.
        clusters: list[list[tuple]] = []
        for c in candidates:
            if clusters and c[0] - clusters[-1][-1][0] <= 12 and np.sign(c[1]) == np.sign(clusters[-1][-1][1]):
                clusters[-1].append(c)
            else:
                clusters.append([c])
        for cl in clusters:
            i, med, same, _ = max(cl, key=lambda c: abs(c[3]))
            start = idx[i]
            w = ly.iloc[i:i + 12]
            nxt = ly.iloc[i + 12:i + 24]
            following = float(np.expm1(nxt.median())) if len(nxt.dropna()) == 12 else None
            if following is None:
                confirmed = False
            else:
                reversed_ = np.sign(nxt.median()) == -np.sign(med) and abs(nxt.median()) >= REVERSAL_SHARE * abs(med)
                confirmed = not reversed_
            if following is not None and not confirmed:
                continue  # a move that reverses is a shock, not a break
            prev = ly.iloc[max(i - 12, 0):i]
            if len(prev.dropna()) == 12 and np.sign(prev.median()) == -np.sign(med) \
                    and abs(prev.median()) >= REVERSAL_SHARE * abs(med):
                continue  # the recovery leg of an earlier shock, not a break
            affected = [start.year] if start.month == 1 else [start.year, start.year + 1]
            breaks.append(LevelBreak(
                measure=measure, start=start, step=float(np.expm1(med)), same_sign=same,
                min_yoy=float(np.expm1(w.min())), max_yoy=float(np.expm1(w.max())),
                following_median=following, confirmed=confirmed, affected_years=affected,
                action="exclude_growth" if confirmed else "review",
                note="" if confirmed else "Too recent to confirm persistence; kept in estimation and flagged for review.",
            ))
    return breaks


def apply_overrides(breaks: list[LevelBreak], cfg: MarketConfig) -> list[LevelBreak]:
    """Apply user decisions from estimation.series_breaks (matched by measure and start year)."""
    for b in breaks:
        for o in cfg.estimation.series_breaks:
            if o.measure == b.measure and o.year in (b.start.year, *b.affected_years):
                b.action = o.action
                b.decided_by = o.decided_by if o.decided_by != "pending" else "config (pending)"
                if o.note:
                    b.note = o.note
    return breaks


def mark_reference(breaks: list[LevelBreak], reference_breaks: list[LevelBreak]) -> list[LevelBreak]:
    for b in breaks:
        b.in_reference = any(
            r.measure == b.measure and abs((r.start - b.start).days) <= 31 * MATCH_MONTHS for r in reference_breaks)
    return breaks


def analyse_breaks(monthly: pd.DataFrame, cfg: MarketConfig,
                   reference_monthly: pd.DataFrame | None = None) -> list[LevelBreak]:
    breaks = apply_overrides(detect_level_breaks(monthly, cfg), cfg)
    if reference_monthly is not None and not reference_monthly.empty:
        mark_reference(breaks, detect_level_breaks(reference_monthly, cfg))
    return breaks


# Columns rescaled for each kind of break so the identities still hold:
#   adr:    ADR, revenue, RevPAR      (occupancy unchanged)
#   demand: demand, revenue           (occupancy and RevPAR scale, ADR unchanged)
#   supply: supply, demand, revenue, inventory (a size change; ratios unchanged)
SPLICE_COLUMNS = {
    "adr": ("adr", "revenue", "revpar"),
    "demand": ("demand", "revenue", "occupancy", "revpar"),
    "supply": ("supply", "demand", "revenue", "inventory_rooms", "available_rooms", "closed_rooms"),
}


def splice_levels(monthly: pd.DataFrame, breaks: list[LevelBreak]) -> pd.DataFrame:
    """Rescale pre-break months so levels are comparable across the break.

    Only breaks with action exclude_growth are spliced. Adds a boolean column
    `spliced` and leaves the input unchanged.
    """
    out = monthly.copy()
    out["spliced"] = False
    for b in breaks:
        if b.action != "exclude_growth":
            continue
        pre = out["period_start"] < b.start
        for col in SPLICE_COLUMNS[b.measure]:
            if col in out.columns:
                out.loc[pre, col] = out.loc[pre, col] * b.factor
        out.loc[pre, "spliced"] = True
    return out
