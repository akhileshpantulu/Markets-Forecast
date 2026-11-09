"""Automated critic checks for driver cards (BUILD_PROMPT 6.6.2 and 6.10).

Errors exclude a card from the simulation. Warnings keep it but are listed for
the critic agent and the user.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from engine.drivers.schema import DriverCard

STATUS_NEEDS_SOURCES = {"announced", "tickets_on_sale", "held", "cancelled"}


def resolve_probability(card: DriverCard, ladder: dict) -> float | None:
    if card.probability is not None:
        return float(card.probability)
    if card.event_status and card.event_status in ladder:
        return float(ladder[card.event_status]["default"])
    return None


def parse_cards(raw: list[dict] | None) -> tuple[list[DriverCard], list[dict]]:
    cards, issues = [], []
    for i, d in enumerate(raw or []):
        try:
            cards.append(DriverCard.model_validate(d))
        except ValidationError as exc:
            cid = d.get("id", f"card {i + 1}") if isinstance(d, dict) else f"card {i + 1}"
            msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
            issues.append({"id": cid, "level": "error", "text": f"Invalid card: {msg}"})
    return cards, issues


def check_card(c: DriverCard, years: list[int], driver_priors: dict) -> list[dict]:
    out: list[dict[str, Any]] = []
    ladder = driver_priors.get("event_status_ladder", {})

    def add(level, text):
        out.append({"id": c.id, "level": level, "text": text})

    p = resolve_probability(c, ladder)
    if p is None:
        add("error", "No probability and no event_status to derive one from.")
    if c.magnitude is None:
        add("info", f"Sized by the {c.calculator.type} calculator when the forecast runs on the market's data.")
        return out
    if c.affects == "supply" and c.magnitude.unit != "rooms":
        add("error", "Supply drivers must state their size in rooms.")
    if c.affects == "shock_probability" and c.magnitude.unit != "multiplier":
        add("error", "Shock-probability drivers must state a multiplier.")
    if c.affects in ("demand", "adr") and c.magnitude.unit not in ("pct_of_level", "pct_points_of_growth"):
        add("error", "Demand and ADR drivers must state their size as a percent of the level.")
    if c.affects == "shock_probability" and c.magnitude.high > driver_priors.get("shock_multiplier_cap", 5.0):
        add("warn", f"Shock multiplier above the cap of {driver_priors.get('shock_multiplier_cap', 5.0)}; it is capped.")
    last = c.timing.peak_year or c.timing.start_year
    if c.timing.start_year > years[-1] or last < years[0]:
        add("error", f"Timing ({c.timing.start_year}) falls outside the forecast years {years[0]} to {years[-1]}.")
    if c.already_in_baseline != "none" and not c.baseline_overlap_note.strip():
        add("error", "already_in_baseline is not 'none' but baseline_overlap_note does not explain the overlap.")
    if c.already_in_baseline == "none" and not c.baseline_overlap_note.strip():
        add("warn", "Explain why none of this is already in the historical baseline (baseline_overlap_note).")
    if c.already_in_baseline == "full":
        add("error", "Marked as fully in the baseline: it would be double counted, so it is not applied.")
    if c.origin == "research":
        if not c.evidence:
            add("warn", "No evidence listed. Every factual claim needs a source.")
        for e in c.evidence:
            if not (e.url and e.publisher and e.published and e.accessed):
                add("warn", f"Evidence '{e.claim[:40]}' lacks url, publisher, published or accessed.")
        if c.event_status in STATUS_NEEDS_SOURCES and len({e.publisher for e in c.evidence}) < driver_priors.get("min_sources_for_status", 2):
            add("warn", f"Event status '{c.event_status}' needs {driver_priors.get('min_sources_for_status', 2)} independent sources.")
    if c.event_status and c.probability is not None and c.event_status in ladder:
        band = ladder[c.event_status]
        if not (band["low"] <= c.probability <= band["high"]) and not c.probability_note.strip():
            add("warn", f"Probability {c.probability:.0%} is outside the {c.event_status} band "
                        f"({band['low']:.0%} to {band['high']:.0%}) with no probability_note.")
    sized = [a.measured_magnitude for a in c.analogs if a.measured_magnitude is not None]
    if sized and max(abs(c.magnitude.high), abs(c.magnitude.low)) > max(abs(x) for x in sized) and not c.magnitude_note.strip():
        add("warn", "Magnitude exceeds the largest analog without a magnitude_note explaining why.")
    if c.origin == "research" and not c.analogs and c.affects != "shock_probability":
        add("warn", "No historical analog to size the effect.")
    return out


def check_cards(cards: list[DriverCard], years: list[int], driver_priors: dict, include_proposed: bool = True
                ) -> tuple[list[DriverCard], list[dict]]:
    """Return (cards to apply, issues)."""
    issues: list[dict] = []
    seen = set()
    keep = []
    for c in cards:
        if c.id in seen:
            issues.append({"id": c.id, "level": "error", "text": "Duplicate id; the later card is ignored."})
            continue
        seen.add(c.id)
        ci = check_card(c, years, driver_priors)
        issues += ci
        if c.status == "rejected":
            issues.append({"id": c.id, "level": "info", "text": "Rejected; not applied."})
            continue
        if c.status == "proposed" and not include_proposed:
            issues.append({"id": c.id, "level": "info", "text": "Proposed and not yet approved; excluded by setting."})
            continue
        if any(i["level"] == "error" for i in ci):
            continue
        keep.append(c)
    return keep, issues
