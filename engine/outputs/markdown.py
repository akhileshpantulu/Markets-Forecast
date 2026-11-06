"""Markdown renderers for history.md, backtest.md and baseline.md."""

from __future__ import annotations

from engine.config import MarketConfig


def p(x, dp=1):
    return "" if x is None else f"{x * 100:+.{dp}f}%"


def lvl(x, dp=1):
    return "" if x is None else f"{x * 100:.{dp}f}%"


def pts(x):
    return "" if x is None else f"{x * 100:+.1f} pts"


def money(x, dp=1):
    return "" if x is None else f"{x:,.{dp}f}"


MEASURE_LABEL = {"occ_growth": "Occupancy growth", "occ_pts": "Occupancy change (pts)", "adr_growth": "ADR growth",
                 "revpar_growth": "RevPAR growth", "demand_growth": "Demand growth", "supply_growth": "Supply growth",
                 "real_adr_growth": "Real ADR growth"}


def render_history(out: dict, cfg: MarketConfig) -> str:
    h = out["history"]
    cur = cfg.currency
    L = [f"# Market history: {cfg.name}", "",
         "Built from the validated monthly CoStar data. Levels before a confirmed series break are spliced; "
         "growth observations containing a break are excluded.", ""]
    L += [f"- {n}" for n in h["notes"]] + [""]
    L += ["## Annual series", "", f"| Year | Occupancy | Occ. change | ADR ({cur}) | ADR growth | RevPAR ({cur}) | RevPAR growth | Demand growth | Supply growth |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in h["annual"]:
        mark = " (COVID)" if r["covid"] else ""
        L.append(f"| {r['year']}{mark} | {lvl(r['occupancy'])} | {pts(r['occ_pts'])} | {money(r['adr'])} | {p(r['adr_growth'])} | "
                 f"{money(r['revpar'])} | {p(r['revpar_growth'])} | {p(r['demand_growth'])} | {p(r['supply_growth'])} |")
    L += ["", "## Growth distributions", "",
          "Percentiles of annual growth. 'All' includes COVID years; 'ex-COVID' excludes them.", "",
          "| Measure | Sample | n | P5 | P10 | P25 | P50 | P75 | P90 | P95 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in h["growth_percentiles"]:
        f = pts if r["measure"] == "occ_pts" else p
        L.append(f"| {MEASURE_LABEL.get(r['measure'], r['measure'])} | {r['sample'].replace('_', '-')} | {r['n']} | " +
                 " | ".join(f(r[f'p{q}']) for q in (5, 10, 25, 50, 75, 90, 95)) + " |")
    L += ["", "## Cycle map", "", "Drawdowns of 3% or more from a running peak in annual nominal RevPAR and demand.", "",
          "| Measure | Peak | Trough | Depth | Recovered | Years to recover | Episode |", "| --- | --- | --- | ---: | --- | ---: | --- |"]
    for r in h["cycles"]:
        rec = "not yet" if r["recovery_year"] is None else str(int(r["recovery_year"]))
        ytr = "" if r["years_to_recover"] is None else str(int(r["years_to_recover"]))
        L.append(f"| {r['measure']} | {r['peak_year']} | {r['trough_year']} | {p(r['depth'])} | {rec} | {ytr} | {r['episode']} |")
    L += ["", "## Supply and demand decomposition", "",
          "Log change in occupancy = log change in demand minus log change in supply.", "",
          "| Year | Occupancy change (log) | From demand | From supply |", "| --- | ---: | ---: | ---: |"]
    for r in h["decomposition"]:
        L.append(f"| {r['year']} | {p(r['dlog_occ'])} | {p(r['from_demand'])} | {p(r['from_supply'])} |")
    sh = h["seasonality_shift"]
    L += ["", "## Seasonality", "",
          f"Average monthly share of annual demand and ADR index (month ADR / annual ADR), early ({sh['early_years']}) "
          f"versus recent ({sh['recent_years']}) complete non-COVID years. Largest change in a month's demand share: "
          f"{sh['max_demand_share_change_pts'] * 100:.1f} pts; largest change in an ADR index: {sh['max_adr_index_change']:.2f}.", "",
          "| Month | Demand share, early | Demand share, recent | ADR index, early | ADR index, recent |", "| --- | ---: | ---: | ---: | ---: |"]
    for r in h["seasonality"]:
        L.append(f"| {r['month']} | {lvl(r['demand_share_early'])} | {lvl(r['demand_share_recent'])} | "
                 f"{r['adr_index_early']:.2f} | {r['adr_index_recent']:.2f} |")
    c = out["models"]["ceiling"]
    L += ["", "## Occupancy range", "",
          f"- Highest annual occupancy outside COVID years: {lvl(h['occ_annual_max'])}; lowest: {lvl(h['occ_annual_min'])}.",
          f"- Occupancy ceiling used by the simulation: {lvl(c['annual'])}. {c['basis']}", ""]
    return "\n".join(L)


def render_backtest(out: dict) -> str:
    b = out["backtest"]
    L = ["# Backtest: baseline engine", "",
         f"Rolling origins {b['origins'][0]} to {b['origins'][-1]}. At each origin the models are refitted on data up to that "
         "year only, and the next five years are simulated and compared with what happened.", ""]
    L += [f"- {n}" for n in b["notes"]] + [""]
    L += ["## Model variants", "",
          "Mean CRPS (lower is better) and P10 to P90 coverage, pooled over measures and horizons.", "",
          "| Variant | CRPS, all outcomes | Coverage, all | CRPS, ex-COVID outcomes | Coverage, ex-COVID |", "| --- | ---: | ---: | ---: | ---: |"]
    for r in b["variants"]:
        mark = " (chosen)" if r["variant"] == b["chosen"] else ""
        L.append(f"| {r['variant']}{mark} | {r['crps_all']:.4f} | {lvl(r['cov80_all'], 0)} | {r['crps_ex_covid']:.4f} | {lvl(r['cov80_ex_covid'], 0)} |")
    L += ["", f"Spread calibration factor: {b['spread']:g}" + (" (applied)." if b["calibrated"] else " (not needed)."), ""]
    L += ["## Coverage by horizon", "",
          "Share of actual outcomes inside the P10 to P90 band (target 80%) and P25 to P75 band (target 50%). "
          "'Ex-COVID' drops outcome years 2020 to 2022. Bias is the median forecast minus the actual, on average.", "",
          "| Measure | Horizon | n | P10-P90, all | P10-P90, ex-COVID | P25-P75, ex-COVID | Bias, ex-COVID |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in b["coverage"]:
        if r["spread"] != b["spread"]:
            continue
        L.append(f"| {MEASURE_LABEL[r['measure']]} | {r['h']} | {r['n']} | {lvl(r['cov80'], 0)} | {lvl(r['cov80_ex'], 0)} | "
                 f"{lvl(r['cov50_ex'], 0)} | {p(r['bias_ex'])} |")
    L += ["", "## Against naive benchmarks (ex-COVID outcomes)", "",
          "Model CRPS against the absolute error of two point forecasts: zero growth and the historical mean growth "
          "known at the origin. For a point forecast, CRPS equals absolute error, so the columns compare directly.", "",
          "| Measure | Horizon | Model CRPS | Model median abs. error | Zero growth abs. error | Historical mean abs. error |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in b["benchmarks"]:
        if r["outcomes"] != "ex_covid":
            continue
        L.append(f"| {MEASURE_LABEL[r['measure']]} | {r['h']} | {lvl(r['model_crps'])} | {lvl(r['model_mae'])} | "
                 f"{lvl(r['zero_mae'])} | {lvl(r['mean_mae'])} |")
    L.append("")
    return "\n".join(L)


def render_baseline(out: dict, cfg: MarketConfig) -> str:
    m = out["meta"]
    cur = cfg.currency
    L = [f"# Baseline forecast: {cfg.name}", "",
         f"**Status: {m['status']}.** Generated by engine {m['engine_version']} with seed {m['seed']} and {m['paths']:,} paths. "
         f"Last actual month {m['last_actual_month']}. Model variant {m['variant']}; ADR mode {m['adr_mode']}.", "",
         "## Headline", "", "Probability-weighted mean, with P10 / P50 / P90.", "",
         "| Year | Occupancy growth | Occupancy change | ADR growth | RevPAR growth | P(RevPAR growth < 0) |", "| --- | --- | --- | --- | --- | ---: |"]
    for r in out["summary"]:
        f = lambda k, fn=p: f"{fn(r[k]['mean'])} ({fn(r[k]['p10'])} / {fn(r[k]['p50'])} / {fn(r[k]['p90'])})"
        L.append(f"| {r['year']} {r['label']} | {f('occ_growth')} | {f('occ_pts', pts)} | {f('adr_growth')} | {f('revpar_growth')} | "
                 f"{r['p_revpar_negative'] * 100:.0f}% |")
    L += ["", f"Levels at P50: " + "; ".join(f"{r['year']} occupancy {lvl(r['occupancy']['p50'])}, ADR {money(r['adr']['p50'])} {cur}"
                                              for r in out["summary"]), ""]
    L += ["## Against history", "", "| Year | Occ. growth percentile | ADR growth percentile | RevPAR growth percentile | Nearest analog | Flags |",
          "| --- | ---: | ---: | ---: | --- | --- |"]
    for r in out["positions"]:
        flags = [k for k in ("occ_growth", "adr_growth", "revpar_growth") if r[k]["outside_p5_p95"]]
        flags = [f"{MEASURE_LABEL[k]} P50 outside history P5-P95" for k in flags]
        if r["occ_above_hist_max"]:
            flags.append("occupancy above historical maximum")
        L.append(f"| {r['year']} | {lvl(r['occ_growth']['percentile'], 0)} | {lvl(r['adr_growth']['percentile'], 0)} | "
                 f"{lvl(r['revpar_growth']['percentile'], 0)} | {r['analog_year']} | {'; '.join(flags) or 'none'} |")
    st = out["stub"]
    if st:
        L += ["", "## Stub year", "",
              f"{len(st['actual'])} actual months. Remaining months use the prior year's months times the recency-weighted "
              f"year-to-date trend (demand {p(st['trend_demand'])}, ADR {p(st['trend_adr'])}), with errors drawn from how this "
              f"rule performed in {len(st['calibration_years'])} past years at the same cutoff.", ""]
    s = out["supply"]
    L += ["## Supply outlook", "", f"Starting from {s['base_available_rooms']:,.0f} available rooms (temporarily closed hotels excluded).", "",
          "| Year | Supply growth (mean) | P10 | P90 |", "| --- | ---: | ---: | ---: |"]
    for r in out["summary"]:
        g = r["supply_growth"]
        L.append(f"| {r['year']} | {p(g['mean'])} | {p(g['p10'])} | {p(g['p90'])} |")
    if s["projects"]:
        years = [k for k in s["projects"][0] if k.isdigit()]
        L += ["", "| Project | Rooms | Stage | Planned | Median opening | " + " | ".join(f"Open by {y}" for y in years) + " |",
              "| --- | ---: | --- | --- | --- | " + " | ".join("---:" for _ in years) + " |"]
        for r in s["projects"]:
            L.append(f"| {r['name']} | {r['rooms']:.0f} | {r['stage'].replace('_', ' ')} | {r['projected_open'] or ''} | "
                     f"{r['median_open'] or 'not within horizon'} | " + " | ".join(lvl(r[y], 0) for y in years) + " |")
    d = out["models"]["demand"]
    a = out["models"]["adr"]
    L += ["", "## Models", "",
          f"- Demand, normal years ({d['n_normal']}): mu {p(d['mu'])} (estimate {p(d['mu_hat'])}, weight on data {d['w_mu']:.2f}), "
          f"phi {d['phi']:.2f} (estimate {d['phi_hat']:.2f}, weight {d['w_phi']:.2f}), residual sd {lvl(d['sigma'])}, "
          f"residuals {d['residual_type']}.",
          f"- Shocks: years {', '.join(map(str, d['shock_years'])) or 'none'}; annual probability moderate {lvl(d['p_moderate'])}, "
          f"severe {lvl(d['p_severe'])} (Beta prior updated with {d['exposure_years']} years of history).",
          f"- ADR ({a['mode']} mode): drift {p(a['alpha'])}, pricing power beta {a['beta']:.2f} (estimate {a['beta_hat']:.2f}, "
          f"weight {a['w_beta']:.2f}), residual persistence {a['rho']:.2f}, residual sd {lvl(a['sigma_u'])}, "
          f"correlation with demand {a['corr']:.2f}.", ""]
    cs = out["covid_sensitivity"]
    L += ["## COVID-years sensitivity", "",
          f"Default: {cs['default']} 2020 to 2022 from estimation. Difference in P50 between the default and the alternative.", "",
          "| Year | Occupancy growth | ADR growth | RevPAR growth |", "| --- | ---: | ---: | ---: |"]
    for r in cs["by_year"]:
        L.append(f"| {r['year']} | {p(r['occ_growth']['difference'])} | {p(r['adr_growth']['difference'])} | {p(r['revpar_growth']['difference'])} |")
    L += ["", "## Gaps", ""] + [f"- {g}" for g in out["gaps"]]
    L += ["", "## Assumptions", ""] + [f"- {x}" for x in out["assumptions"]]
    if out["notes"]:
        L += ["", "## Notes", ""] + [f"- {x}" for x in out["notes"]]
    L.append("")
    return "\n".join(L)
