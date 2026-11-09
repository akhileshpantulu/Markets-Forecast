"""Readable ledger forecast (markdown): headline, then every year's lines with their reasons."""

from __future__ import annotations


def _p(x, dp=1):
    return "" if x is None else f"{x * 100:+.{dp}f}%"


def _l(x, dp=1):
    return "" if x is None else f"{x * 100:.{dp}f}%"


BASIS = {"fitted": "fitted on European data", "assumption": "assumption", "user_decision": "your decision",
         "fact": "fact", "driver (approved)": "driver, approved", "driver (proposed)": "driver, proposed"}


def render(out: dict, market: str) -> str:
    m, b, f5 = out["meta"], out["baseline"], out["five_year"]
    cur = m["currency"]
    L = [f"# Forecast: {market}", "",
         f"Ledger forecast, engine {m['engine_version']}, data to {m['last_actual_month']}, calibration {m['calibrated_on']} "
         f"(panel snapshot {m['panel_snapshot']}, registry {m['registry_version']}). "
         + ("Provisional: some drivers are still proposed." if m["provisional"] else ""), "",
         "Each year's forecast is the sum of the lines below it. Each line states its basis: a fact, a value fitted on "
         "European submarket data, a labelled assumption, your decision, or a sourced driver weighted by its chance.", "",
         "## Headline", "", "| Year | Occupancy | ADR | RevPAR | RevPAR P10 to P90 | Occupancy level | ADR level |",
         "| --- | ---: | ---: | ---: | --- | ---: | ---: |"]
    for y in out["years"]:
        r = y["range"]["revpar_growth"]
        L.append(f"| {y['label']} | {_p(y['occ_growth'])} | {_p(y['adr_growth'])} | **{_p(y['revpar_growth'])}** | "
                 f"{_p(r['p10'])} to {_p(r['p90'])} | {_l(y['occupancy'])} | {y['adr']:,.1f} {cur} |")
    L += ["", f"Five-year RevPAR CAGR {f5['from']} to {f5['to']}: **{_p(f5['revpar_cagr'])}** (P10 {_p(f5['p10'])}, P90 {_p(f5['p90'])}), "
          f"against the long-run {_p(f5['baseline_revpar_cagr'])} ({_p(f5['difference'])} points).", ""]
    L += ["## Baseline", "", f"Long-run CAGR {b['start']} to {b['end']}: RevPAR {_p(b['revpar'])}, occupancy {_p(b['occupancy'])}, "
          f"ADR {_p(b['adr'])}, supply {_p(b['supply'])}, demand {_p(b['demand'])}"
          + (f", inflation {_l(b['cpi_mean'])} a year" if b.get("cpi_mean") is not None else "") + ". "
          + ("Occupancy is held flat and rate carries the RevPAR growth." if b["split"] == "flat" else "Occupancy and ADR grow at their own rates."), ""]
    for y in out["years"]:
        L += [f"## {y['label']}: RevPAR {_p(y['revpar_growth'])}", "",
              f"Occupancy {_p(y['occ_growth'])}, ADR {_p(y['adr_growth'])}. Range for RevPAR {_p(y['range']['revpar_growth']['p10'])} to "
              f"{_p(y['range']['revpar_growth']['p90'])}; {y['range']['p_revpar_fall']:.0%} chance it falls.", "",
              "| Layer | Line | Occupancy | ADR | Basis |", "| --- | --- | ---: | ---: | --- |"]
        for ln in y["lines"]:
            L.append(f"| {ln['layer']} | {ln['title']} | {ln['occ'] * 100:+.2f} | {ln['adr'] * 100:+.2f} | {BASIS.get(ln['basis'], ln['basis'])} |")
        L.append(f"| | **Total** | **{y['occ_growth'] * 100:+.2f}** | **{y['adr_growth'] * 100:+.2f}** | |")
        L.append("")
        for ln in y["lines"]:
            L.append(f"- **{ln['title']}.** {ln['text']}" + (f" Working: {ln['evidence']}" if ln["layer"] == "Drivers" and ln.get("evidence") else ""))
        L.append("")
    d = out["drivers"]
    if d["applied"]:
        L += ["## Drivers", "", "| Driver | Affects | Chance | Sized by | Status |", "| --- | --- | ---: | --- | --- |"]
        for a in d["applied"]:
            L.append(f"| {a['title']} | {a['affects'].replace('_', ' ')} | {a['probability']:.0%} | {a['calculator'] or 'stated range'} | {a['status']} |")
        L.append("")
        for a in d["applied"]:
            if a["working"]:
                L += [f"**{a['title']}**", ""] + [f"- {w}" for w in a["working"]] + [""]
    s = out["supply"]
    L += ["## Supply", "", f"Inventory trend {_p(s['inventory_trend'])} a year. Market priced like {s['average_tier']} (assumption).", "",
          "| Year | Available supply | Inventory | Against trend | New builds | Reopenings | Unannounced | Removed |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in s["years"]:
        L.append(f"| {r['year']} | {_p(r['supply_growth'])} | {_p(r['inventory_growth'])} | {r['excess_vs_trend'] * 100:+.1f} pts | "
                 f"{r['new_builds_rooms']:,.0f} | {r['reopening_rooms']:,.0f} | {r['unannounced_rooms']:,.0f} | {r['removed_rooms']:,.0f} |")
    L += ["", "| Project | Rooms | Stage | Tier | Product | Chance of opening in the horizon | Median opening |",
          "| --- | ---: | --- | --- | --- | ---: | --- |"]
    for p in s["projects"]:
        L.append(f"| {p['name']} | {p['rooms']:,.0f} | {p['stage'].replace('_', ' ')} | {p['tier']} | {p['product'] or 'hotel'} | "
                 f"{p['p_open_horizon']:.0%} | {p['median_open'] or ''} |")
    rg = out["ranges"]
    L += ["", "## Ranges", "", rg["note"], f"Source: {rg['source']}",
          f"Volatility relative to the European norm: occupancy x{rg['volatility_ratio']['occ']:.2f}, ADR x{rg['volatility_ratio']['adr']:.2f}.", ""]
    ev = out["calibration"]["evidence"].get("cagr_rule_backtest")
    if ev:
        L += [f"How good is the long-run CAGR rule? Across {ev['n']} five-year periods in European submarkets it missed the five-year "
              f"RevPAR CAGR by {ev['five_year_revpar_cagr_mae'] * 100:.1f} points a year on average; adjusting for supply growth against "
              f"trend cut that to {ev['mae_with_supply_adjustment'] * 100:.1f} points.", ""]
    if out["adjustments"]:
        L += ["## Your adjustments", ""] + [f"- {a}" for a in out["adjustments"]] + [""]
    if out["gaps"]:
        L += ["## Gaps", ""] + [f"- {g}" for g in out["gaps"]] + [""]
    return "\n".join(L)
