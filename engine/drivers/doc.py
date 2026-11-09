"""Build the drivers deliverable for one market.

python -m engine.drivers.doc <market-slug> [card files...] [--stub-year YYYY]

Merges the researchers' card files (default: runs/<market>/drivers_*.yaml), runs the automated
checks, and writes:
  runs/<market>/drivers.yaml  the file to drop into the Drivers slot on the web page
  runs/<market>/drivers.md    a readable document: every card's reasoning, chance, size, sources, checks
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from engine.config import REPO_ROOT, load_market_config
from engine.drivers.schema import DriverCard
from engine.drivers.validate import check_cards, parse_cards, resolve_probability
from engine.forecast import load_driver_priors

AFFECTS = {"demand": "Demand", "adr": "ADR", "supply": "Supply", "shock_probability": "Chance of a downturn"}
SHAPE = {"one_off": "one-off (that year only; the next year grows from a normal base)",
         "temporary": "temporary (fades over the decay years)", "step": "permanent from the year it reaches full size"}


def _size(c: DriverCard) -> str:
    if c.magnitude is None:
        ins = ", ".join(f"{k} {v}" for k, v in (c.calculator.inputs or {}).items())
        return f"sized by the {c.calculator.type.replace('_', ' ')} calculator from: {ins}"
    m = c.magnitude
    if m.unit == "rooms":
        return f"{m.low:g} to {m.high:g} rooms, most likely {m.mode:g}"
    if m.unit == "multiplier":
        return f"{m.low:g}x to {m.high:g}x the market's usual chance of a downturn, most likely {m.mode:g}x"
    return f"{m.low:+g}% to {m.high:+g}% of the year's level, most likely {m.mode:+g}%"


STATUSES = {"card", "no_material_effect", "not_applicable", "could_not_verify"}
STATUS_TEXT = {"card": "driver card(s)", "no_material_effect": "checked, no material effect", "not_applicable": "not applicable",
               "could_not_verify": "could not verify", "missing": "NOT RESEARCHED"}


def load_checklist(root: Path = REPO_ROOT) -> list[dict]:
    p = root / "config" / "research_checklist.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("items", []) if p.exists() else []


def check_coverage(checklist: list[dict], entries: list[dict], card_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """One row per checklist item with its answer, and issues: unanswered items are errors."""
    by = {}
    for e in entries:
        by.setdefault(e.get("item"), []).append(e)
    rows, issues = [], []
    known = {i["id"] for i in checklist}
    for e in entries:
        if e.get("item") not in known:
            issues.append({"id": f"coverage:{e.get('item')}", "level": "warn", "text": "Coverage entry for an item not in the checklist."})
    for it in checklist:
        es = by.get(it["id"], [])
        if not es:
            rows.append({**it, "status": "missing", "cards": [], "finding": ""})
            issues.append({"id": f"coverage:{it['id']}", "level": "error", "text": f"Not researched: {it['title']}"})
            continue
        for e in es:
            st = e.get("status")
            cards = list(e.get("cards") or [])
            if st not in STATUSES:
                issues.append({"id": f"coverage:{it['id']}", "level": "error", "text": f"Unknown status {st!r}."})
            if st == "card" and not cards:
                issues.append({"id": f"coverage:{it['id']}", "level": "error", "text": "Status is card but no card ids are given."})
            for c in cards:
                if c not in card_ids:
                    issues.append({"id": f"coverage:{it['id']}", "level": "error", "text": f"Card {c} is not in the merged drivers."})
            if st in ("no_material_effect", "not_applicable") and not (e.get("sources") or e.get("finding")):
                issues.append({"id": f"coverage:{it['id']}", "level": "error", "text": "A nil finding needs the sources checked or the reason."})
            if st == "could_not_verify":
                issues.append({"id": f"coverage:{it['id']}", "level": "warn", "text": f"Could not verify: {e.get('finding', '')}"})
            rows.append({**it, "status": st, "cards": cards, "finding": e.get("finding", ""), "sources": e.get("sources", [])})
    return rows, issues


def render(cards: list[DriverCard], issues: list[dict], keep_ids: set[str], market_name: str, years: list[int],
           ladder: dict, sources: list[str], coverage: list[dict] | None = None) -> str:
    today = date.today().isoformat()
    by_id: dict[str, list[dict]] = {}
    for i in issues:
        by_id.setdefault(i["id"], []).append(i)
    L = [f"# Drivers: {market_name}", "",
         f"Generated {today} for forecast years {years[0]} to {years[-1]}. "
         "Drop `drivers.yaml` (next to this file) into the Drivers slot on the forecast page, then switch drivers on or "
         "off and change their chances on the Assumptions tab.", "",
         "Each driver happens in a share of simulated paths equal to its chance, with a size drawn from its range. "
         "Facts carry sources; chances and sizes are judgements and are labelled as such.", ""]
    n_app = sum(1 for c in cards if c.status == "approved")
    n_prop = sum(1 for c in cards if c.status == "proposed")
    n_rej = sum(1 for c in cards if c.status == "rejected")
    L += [f"**Status:** {len(cards)} cards: {n_app} approved, {n_prop} proposed (awaiting approval), {n_rej} rejected. "
          f"{len(keep_ids)} pass the automated checks and will be applied." + (" Provisional until every card is approved." if n_prop else ""), ""]
    L += ["## Summary", "", "| Driver | Affects | Timing | Chance | Size if it happens | Confidence | Status |",
          "| --- | --- | --- | ---: | --- | --- | --- |"]
    for c in cards:
        p = resolve_probability(c, ladder)
        t = c.timing
        timing = f"{t.start_year}" + (f" to {t.peak_year}" if t.peak_year and t.peak_year != t.start_year else "")
        mark = "" if c.id in keep_ids else " (not applied)"
        L.append(f"| {c.title}{mark} | {AFFECTS[c.affects]} | {timing}, {c.resolved_shape().replace('_', '-')} | "
                 f"{'' if p is None else f'{p * 100:.0f}%'} | {_size(c)} | {c.confidence} | {c.status} |")
    L.append("")
    for n, c in enumerate(cards, 1):
        p = resolve_probability(c, ladder)
        L += [f"## {n}. {c.title}", "", f"`{c.id}` | {AFFECTS[c.affects]} | {c.category.replace('_', ' ')} | segment: {c.segment} | "
              f"confidence: {c.confidence} | status: **{c.status}**", ""]
        if c.mechanism_chain:
            L += ["**How it works**", ""] + [f"{m.order}. ({m.sign}) {m.effect}" for m in sorted(c.mechanism_chain, key=lambda x: x.order)] + [""]
        t = c.timing
        L += ["**Timing and size (judgement)**", "",
              f"- From {t.start_year}" + (f", full size in {t.peak_year}" if t.peak_year else "")
              + (f", fading over {t.decay_years} years" if t.decay_years else "")
              + (f", months {', '.join(map(str, t.months))}" if t.months else "") + f": {SHAPE[c.resolved_shape()]}.",
              f"- Size if it happens: {_size(c)}." + (f" {c.magnitude_note}" if c.magnitude_note else ""),
              f"- Chance: {'' if p is None else f'{p * 100:.0f}%'}"
              + (f" (event status: {c.event_status.replace('_', ' ')}"
                 + (", from the ladder" if c.probability is None else "") + ")" if c.event_status else "")
              + (f". {c.probability_note}" if c.probability_note else "."),
              f"- Already in the historical baseline: {c.already_in_baseline}. {c.baseline_overlap_note}", ""]
        if c.conditional_on:
            L += [f"Moves together with the scenario(s): {', '.join(c.conditional_on)}.", ""]
        if c.counter_effects:
            L += [f"Offset by: {', '.join(c.counter_effects)}.", ""]
        if c.analogs:
            L += ["**Historical analogs used for sizing**", ""]
            L += [f"- {a.event} ({a.market}): {a.measured_effect}" + (f" [{a.measured_magnitude:g}]" if a.measured_magnitude is not None else "")
                  + f". Source: {a.source}" for a in c.analogs] + [""]
        if c.evidence:
            L += ["**Evidence (facts)**", ""]
            L += [f"- {e.claim} Source: [{e.publisher}]({e.url}), published {e.published}, accessed {e.accessed}." for e in c.evidence] + [""]
        if c.reviewer_note:
            L += [f"**Critic:** {c.reviewer_note}", ""]
        chk = [i for i in by_id.get(c.id, []) if i["level"] in ("error", "warn")]
        if chk:
            L += ["**Automated checks**", ""] + [f"- {i['level']}: {i['text']}" for i in chk] + [""]
    if coverage is not None:
        done = sum(1 for r in coverage if r["status"] != "missing")
        L += ["## Research coverage", "",
              f"{done} of {len({r['id'] for r in coverage})} checklist items answered (config/research_checklist.yaml). "
              "Every item needs a driver card or a recorded nil finding with the sources checked.", "",
              "| Item | Researcher | Answer | Cards | Finding |", "| --- | --- | --- | --- | --- |"]
        for r in coverage:
            src = "; ".join(f"[{x.get('publisher', 'source')}]({x.get('url', '')})" for x in (r.get("sources") or [])[:3])
            L.append(f"| {r['title']} | {r['owner']} | {STATUS_TEXT.get(r['status'], r['status'])} | {', '.join(r['cards'])} | "
                     f"{(r['finding'] or '').replace('|', '/')}{(' Sources: ' + src) if src else ''} |")
        L.append("")
    L += ["## Files merged", ""] + [f"- {s}" for s in sources] + [""]
    return "\n".join(L)


def build(market: str, files: list[Path] | None = None, stub_year: int | None = None, root: Path = REPO_ROOT) -> tuple[Path, Path, list[dict]]:
    cfg = load_market_config(market, root / "config")
    run_dir = root / "runs" / market
    files = files or sorted(p for p in run_dir.glob("drivers_*.yaml"))
    raw: list[dict] = []
    for f in files:
        doc = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
        raw += doc.get("drivers", []) if isinstance(doc, dict) else doc
    y0 = stub_year or date.today().year
    years = [y0 + i for i in range(cfg.horizon_years + 1)]
    dp = load_driver_priors(root / "config")
    cards, issues = parse_cards(raw)
    keep, more = check_cards(cards, years, dp, include_proposed=True)
    issues += more
    run_dir.mkdir(parents=True, exist_ok=True)
    out_yaml = run_dir / "drivers.yaml"
    header = (f"# Drivers for {cfg.name}, generated {date.today().isoformat()} by python -m engine.drivers.doc.\n"
              "# Drop this file into the Drivers slot on the forecast page. Edit `status` to approve or reject a card,\n"
              "# or change chances on the page's Assumptions tab. Readable version: drivers.md.\n")
    out_yaml.write_text(header + yaml.safe_dump({"drivers": [c.model_dump(exclude_none=True) for c in cards]},
                                                sort_keys=False, allow_unicode=True), encoding="utf-8")
    cov_entries = []
    for f in sorted(run_dir.glob("coverage_*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cov_entries += d.get("coverage", []) if isinstance(d, dict) else d
    checklist = load_checklist(root)
    coverage, cov_issues = check_coverage(checklist, cov_entries, {c.id for c in cards}) if checklist else (None, [])
    issues += cov_issues
    out_md = run_dir / "drivers.md"
    out_md.write_text(render(cards, issues, {c.id for c in keep}, cfg.name, years, dp.get("event_status_ladder", {}),
                             [str(Path(f).relative_to(root)) if Path(f).is_relative_to(root) else str(f) for f in files],
                             coverage),
                      encoding="utf-8")
    return out_yaml, out_md, issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge, check and document driver cards for one market.")
    ap.add_argument("market")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--stub-year", type=int, default=None)
    a = ap.parse_args(argv)
    y, m, issues = build(a.market, a.files or None, a.stub_year)
    errs = [i for i in issues if i["level"] == "error"]
    print(f"wrote {y}\nwrote {m}\n{len(errs)} errors, {sum(1 for i in issues if i['level'] == 'warn')} warnings")
    for i in errs[:60]:
        print("  error:", i["id"], i["text"])
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
