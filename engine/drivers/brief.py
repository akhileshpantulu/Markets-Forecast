"""Research brief for running the driver research outside this repository (for example in Claude Cowork).

The brief is self-contained: the checklist, the method, the card format, the calculators and the exact
output file. The output (one YAML file with `drivers` and `coverage`) is dropped into the forecast page's
Drivers slot, which checks the cards and the coverage.

python -m engine.drivers.brief                 # writes cowork/hotel-market-research/SKILL.md (generic)
The page builds a market-specific brief after a run (engine.web_api), with the market, the months still
to cover and the pipeline projects to verify.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from engine.config import CONFIG_DIR, REPO_ROOT

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
OWNER = {"events": "Events and place", "demand": "Access, source markets, corporate and policy", "supply": "Supply",
         "geopolitics": "Risk"}

CARD_EXAMPLES = '''```yaml
drivers:
  # An event sized from organiser figures (event calculator)
  - id: maison-objet-jan-2028
    title: Maison&Objet moves from January to February in 2028
    category: mice               # air_access supply events macro fx geopolitical policy corporate mice leisure_sentiment regulation infrastructure other
    affects: demand              # demand | adr | supply | shock_probability
    timing: {start_year: 2028, months: [1]}
    shape: one_off               # one_off (that year, pays back next year) | temporary (+ decay_years) | step (permanent)
    event_status: announced      # rumour | credible_report | announced | tickets_on_sale | held | cancelled
    calculator:
      type: event
      inputs: {attendees: [70000, 80000, 90000], out_of_town_share: 0.55, nights: 2.5, capture_share: 0.06, months: [1],
               adr_compression: [0.02, 0.05, 0.08]}
    magnitude_note: Attendance and out-of-town share from the organiser's 2026 report; capture share is an assumption.
    mechanism_chain:
      - {order: 1, sign: "-", effect: January loses the fair's room nights}
      - {order: 2, sign: "-", effect: Rate compression in January eases}
    already_in_baseline: partial
    baseline_overlap_note: The fair recurs every year (in the baseline); only the month shift is new.
    evidence:
      - {claim: The 2028 edition runs 3 to 7 February., url: "https://...", publisher: Organiser, published: "2026-10-02", accessed: "2026-11-10"}
      - {claim: Dates confirmed by the convention centre calendar., url: "https://...", publisher: Venue, published: "2026-10-05", accessed: "2026-11-10"}
    confidence: medium
    status: proposed

  # New routes (air_capacity calculator)
  - id: new-longhaul-routes-2027
    title: Two new long-haul routes from spring 2027
    category: air_access
    affects: demand
    shape: step
    timing: {start_year: 2027, peak_year: 2028}
    probability: 0.8
    calculator: {type: air_capacity, inputs: {seats: [250000, 300000, 350000], capture_share: 0.04, already_in_trend_share: 0.3}}
    already_in_baseline: partial
    baseline_overlap_note: The airport adds routes every year; 30% of these seats are taken as normal growth.
    evidence: [...]
    status: proposed

  # A stated size (no calculator), and a risk card
  - id: tourist-tax-2027
    title: Tourist tax doubles from January 2027
    category: policy
    affects: demand
    shape: step
    timing: {start_year: 2027}
    probability: 0.9
    calculator: {type: price_change, inputs: {price_change: 0.012, absorbed_by_hotels: 0.3}}
    already_in_baseline: none
    baseline_overlap_note: New rate; not in history.
    evidence: [...]
    status: proposed
  - id: strike-risk-2027
    title: Pension reform strikes raise disruption risk in 2027
    category: geopolitical
    affects: shock_probability
    timing: {start_year: 2027}
    probability: 0.4
    magnitude: {low: 1.1, mode: 1.3, high: 1.6, unit: multiplier}
    already_in_baseline: partial
    baseline_overlap_note: Strikes recur; the multiplier covers only the risk above the usual rate.
    evidence: [...]
    status: proposed

coverage:
  - item: cal.recurring
    status: card                 # card | no_material_effect | not_applicable | could_not_verify
    cards: [maison-objet-jan-2028]
    finding: One date shift found; all other recurring fairs keep their months.
    sources: [{publisher: Convention bureau, url: "https://...", accessed: "2026-11-10"}]
    searches: ["salons paris 2027 2028 dates", "trade fair calendar paris 2028"]
  - item: risk.climate
    status: no_material_effect
    finding: No climate risk beyond the historical pattern; heat waves are already in the summer history.
    sources: [{publisher: National weather service, url: "https://...", accessed: "2026-11-10"}]
    searches: ["canicule paris tourisme impact", "paris heatwave hotel demand"]
```'''

CALCULATORS = [
    ("event", "attendees, out_of_town_share, nights, guests_per_room, hotel_share, capture_share, displacement_share, adr_compression, months; "
              "or event_type (mega_multi_sport, major_football_tournament, rugby_world_cup, year_long_religious) with months to use a "
              "measured analog when attendance is not published"),
    ("air_capacity", "seats (extra seats a year), load_factor, visitor_share, nights, guests_per_room, hotel_share, capture_share, already_in_trend_share"),
    ("source_market", "share_of_demand, and change_in_arrivals, or driver_change with elasticity; optional adr_sensitivity"),
    ("price_change", "price_change (e.g. a tax as a share of the room rate), elasticity, absorbed_by_hotels"),
    ("competing_supply", "units_change (e.g. -800 short-term rental units), unit_occupancy, shift_to_hotels, capture_share"),
]


def _checklist() -> list[dict]:
    return yaml.safe_load((CONFIG_DIR / "research_checklist.yaml").read_text(encoding="utf-8"))["items"]


def _defaults() -> list[str]:
    reg = yaml.safe_load((CONFIG_DIR / "calibration.yaml").read_text(encoding="utf-8"))
    out = []
    for k, p in reg["params"].items():
        if k.startswith("calc.") or (k.startswith("events.") and k.endswith((".adr", ".occ"))):
            v = p["value"]
            out.append(f"- {p['label']}: {v * 100:.0f}%" if p.get("kind") == "percent" else f"- {p['label']}: {v:g}")
    return out


def render_brief(market: dict | None = None, projects: list[dict] | None = None) -> str:
    """market: {name, slug, costar_market, costar_submarket, country, currency, last_actual_month 'YYYY-MM', horizon}."""
    items = _checklist()
    mk = market or {}
    slug = mk.get("slug", "<market-slug>")
    name = mk.get("name", "<market name>")
    L = []
    if not market:
        L += ["---", "name: hotel-market-research",
              "description: Research forward-looking drivers for a hotel market forecast (events, air access, source markets, policy, "
              "supply, risk) against a 45-item checklist, and save one drivers YAML file to drop into the forecast page.", "---", ""]
    L += [f"# Driver research brief: {name}", "",
          "You are researching what will make this hotel market's occupancy and rate differ from its long-run trend over the "
          "forecast years. The forecast page already holds the market's history, its long-run growth and its hotel pipeline. Your "
          "job is the drivers: sourced, sized and weighted by their chance. Leave no stone unturned: every checklist item below must "
          "be answered, either with driver cards or with a recorded finding that nothing material applies.", ""]
    if market:
        y, mth = (int(x) for x in mk["last_actual_month"].split("-"))
        rest = ", ".join(MONTHS[i] for i in range(mth, 12)) or "none"
        L += ["## The market", "",
              f"- Market: {name} (CoStar market {mk.get('costar_market') or 'n/a'}, submarket {mk.get('costar_submarket') or 'n/a'}), "
              f"{mk.get('country', '')}, currency {mk.get('currency', '')}.",
              f"- Data runs to {MONTHS[mth - 1]} {y}. The current year ({y}) is forecast at its year-to-date growth, so for {y} you "
              f"research only the remaining months ({rest} {y}): events that were not there in the same months of {y - 1}, and "
              f"{y - 1} events in those months that do not recur.",
              f"- Forecast years: {y + 1} to {y + int(mk.get('horizon', 5))}. Also check {y - 1} and {y} for one-offs that will "
              "not recur (they lower the following year).", ""]
    else:
        L += ["## Before you start", "",
              "Ask for, or read from the attached brief: the market and submarket, the country, the currency, the last month of "
              "actual data, and the forecast years.", ""]
    L += ["## How to research", "",
          "- At least three searches per checklist item, including one in the local language (French for Paris, German for Berlin, "
          "Italian for Rome, Spanish for Madrid, and so on).",
          "- Open the pages, not just the search results. Prefer primary sources: organisers, convention bureaus, airports and "
          "airlines, statistics offices, city and national governments, central banks, company filings.",
          "- Event status taken from news needs two independent sources. Never rely on memory for a current status; search now and "
          "record published and accessed dates.",
          "- For every finding, write the chain of effects: first order (the direct demand or rate change), second order (the "
          "market's reaction: displacement, pricing, airline or supply response), third order (slower knock-ons). Offsetting effects "
          "go on the same card or a linked card.",
          "- The baseline is the market's long-run growth, which already contains its normal calendar and its usual share of "
          "shocks. Only differences from a normal year count. A fair that recurs every year unchanged is not a driver; a fair that "
          "moves month, skips a year or doubles in size is.",
          "- Do not use, copy or quote CoStar forecasts or CoStar data in evidence.", ""]
    L += ["## Checklist (answer every item)", "", "| Id | Area | What to check |", "| --- | --- | --- |"]
    L += [f"| `{it['id']}` | {OWNER.get(it['owner'], it['owner'])} | {it['title']} |" for it in items]
    L.append("")
    if projects:
        L += ["## Pipeline projects to verify (`sup.pipeline_check`)", "",
              "Find each project's current status and opening date in public sources (planning portals, developer and brand "
              "announcements, press). Report changes as cards only where the project is missing, cancelled, much delayed or "
              "accelerated, or opens at a different tier.", "",
              "| Project | Rooms | Stage | Tier |", "| --- | ---: | --- | --- |"]
        L += [f"| {p.get('name')} | {p.get('rooms', 0):,.0f} | {str(p.get('stage', '')).replace('_', ' ')} | {p.get('tier') or ''} |" for p in projects]
        L.append("")
    L += ["## Sizing with calculators", "",
          "Give inputs and let the forecast compute the size; the page shows the arithmetic. Any input can be a single number or "
          "[low, most likely, high]. Every input is either sourced (in evidence) or labelled an assumption in `magnitude_note`. "
          "Put `calculator` on the card and leave `magnitude` out.", ""]
    L += [f"- `{t}`: {ins}." for t, ins in CALCULATORS]
    L += ["", "Defaults the forecast uses when an input is left out:", ""] + _defaults()
    L += ["", "Without a calculator, give `magnitude` as `{low, mode, high, unit}`: unit `pct_of_level` for demand and ADR (1.0 = +1% "
          "of that year's level), `rooms` for supply, `multiplier` for `shock_probability` (1.5 = 50% more likely than the market's "
          "usual chance of a downturn).", "",
          "## Chances", "",
          "Give `probability` (0 to 1), or `event_status` and let the ladder set it: rumour 17.5%, credible report 40%, announced 80%, "
          "tickets on sale 90%, held 100%, cancelled 0%.", ""]
    L += ["## Output: one file", "",
          f"Save one file named `drivers-{slug}.yaml` with two lists, `drivers` and `coverage`, in this format:", "", CARD_EXAMPLES, "",
          "Rules for the file:",
          "- `id`: lowercase words joined by hyphens, unique.",
          "- `timing.months`: set it whenever the effect falls in specific months; it drives the monthly roll-up and the current year.",
          "- `already_in_baseline` (none, partial, full) with `baseline_overlap_note` explaining what the long-run trend already holds.",
          "- Supply cards: `affects: supply`, `magnitude` in rooms, and `tier` (Luxury, Upper Upscale, Upscale, Upper Midscale, Midscale, Economy).",
          "- Every card has `status: proposed` and at least one evidence item with url, publisher, published and accessed.",
          "- `coverage` has an entry for every checklist id. A nil finding still lists the sources checked and the searches run. "
          "`could_not_verify` says what was tried.", "",
          "## Before saving: self-check", "",
          "- Every checklist id appears in `coverage`; every card id named in coverage exists in `drivers`.",
          "- No card double-counts another, and no card covers something that recurs every year unchanged.",
          "- One-offs in the last two years that will not recur are included (they reduce growth the year after).",
          "- The remaining months of the current year were checked against the same months last year.",
          "- Positive and negative effects of the same event appear together (displacement, payback).",
          "- Sizes stay within their analogs, or `magnitude_note` says why not.", "",
          "## Then", "",
          "Drop the file into the Drivers slot on the forecast page and run. The page lists any card it cannot use and any checklist "
          "item left unanswered, and shows every driver's line in each year's ledger.", ""]
    return "\n".join(L)


SKILL_PATH = REPO_ROOT / "cowork" / "hotel-market-research" / "SKILL.md"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the generic research skill for Cowork.")
    ap.parse_args(argv)
    SKILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_PATH.write_text(render_brief(), encoding="utf-8")
    print(f"wrote {SKILL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
