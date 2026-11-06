"""Why the forecast sits where it sits.

Builds, from the simulation's own trace:
  - a bridge per forecast year that splits mean log growth into components
    (they add up exactly to the mean log growth of demand, supply and ADR);
  - plausibility flags against the market's own history;
  - plain-language sentences generated from those numbers.
No number here is typed by hand: every figure comes from the model or the data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.adr.model import AdrModel
from engine.demand.baseline import DemandModel
from engine.simulate.baseline import Paths
from engine.supply.pipeline import SupplyPaths

CEILING_FLAG_SHARE = 0.25
SUPPLY_GAP_FLAG = 0.01


def pp(x: float, dp: int = 1) -> str:
    """Log change in points of growth, signed."""
    return f"{x * 100:+.{dp}f}"


def pct(x: float, dp: int = 1) -> str:
    return f"{x * 100:+.{dp}f}%"


def _supply_split(sp: SupplyPaths, trace: dict, years: list[int]) -> list[dict]:
    """Split each year's mean supply change into existing hotels, removals, new builds, reopenings, unannounced."""
    month_years = np.array([m.year for m in sp.months])
    days = sp.days[None, :]
    comp = {k: v * days for k, v in sp.components.items()}
    rest = trace["y0_rest_idx"]
    s0 = trace["y0_supply"]
    out = []
    prev = {k: comp[k][:, rest].sum(axis=1) if len(rest) else np.zeros(len(s0)) for k in ("new_builds", "reopenings", "unannounced", "removed")}
    prev_total = s0
    prev["existing"] = s0 - prev["new_builds"] - prev["reopenings"] - prev["unannounced"]
    for y in years[1:]:
        sel = month_years == y
        cur = {k: comp[k][:, sel].sum(axis=1) for k in ("new_builds", "reopenings", "unannounced", "removed")}
        total = sum(comp[k][:, sel].sum(axis=1) for k in ("stock_after_removals", "new_builds", "reopenings", "unannounced"))
        cur["existing"] = total - cur["new_builds"] - cur["reopenings"] - cur["unannounced"]
        g = total / prev_total - 1
        scale = np.where(np.abs(g) > 1e-12, np.log1p(g) / np.where(np.abs(g) > 1e-12, g, 1.0), 1.0)
        d = {k: ((cur[k] - prev[k]) / prev_total * scale).mean() for k in ("new_builds", "reopenings", "unannounced")}
        rem = (-(cur["removed"] - prev["removed"]) / prev_total * scale).mean()
        existing_total = ((cur["existing"] - prev["existing"]) / prev_total * scale).mean()
        d["removals"] = rem
        d["existing_level"] = existing_total - rem           # base level and seasonality carried from the latest month
        out.append({k: float(v) for k, v in d.items()})
        prev, prev_total = cur, total
    return out


def _windows(annual: pd.DataFrame, measure: str, flag: str, length: int = 5) -> np.ndarray:
    a = annual.set_index("year")
    vals = []
    for y in a.index:
        yrs = list(range(y + 1, y + length + 1))
        if any(t not in a.index for t in yrs):
            continue
        sub = a.loc[yrs]
        if sub["covid"].any() or (~sub[flag]).any():
            continue
        vals.append(a.loc[y + length, measure] / a.loc[y, measure] - 1)
    return np.asarray(vals)


def build_explanation(paths: Paths, trace: dict, sp: SupplyPaths, dm: DemandModel, am: AdrModel, hist,
                      summary: list[dict], positions: list[dict], ceiling: float, hist_supply_growth: float | None,
                      cpi_path: dict | None, anchor=None, basis: str = "fitted_trend",
                      supply_trend: float | None = None, shock_drag0: float | None = None,
                      stub_ytd: dict | None = None, stub_info: dict | None = None, cpi_sources: dict | None = None) -> dict:
    years = paths.years
    tr = trace["years"]
    sup = _supply_split(sp, trace, years)
    bridge = []
    for j, y in enumerate(years[1:]):
        t, s = tr[j], sup[j]
        occ_total = t["demand_total"] - t["supply_total"]
        bridge.append({
            "year": y,
            "demand": {"trend": t["demand_trend"], "momentum": t["demand_momentum"], "shocks": t["demand_shocks"],
                       "drivers": t.get("demand_drivers", 0.0),
                       "new_hotels": t["demand_new_hotels"], "ceiling": t["demand_ceiling"], "noise": t["demand_noise"],
                       "total": t["demand_total"]},
            "supply": {**s, "drivers": t.get("supply_drivers", 0.0), "total": t["supply_total"]},
            "occupancy_total": occ_total,
            "adr": {"inflation": t["adr_inflation"], "real_drift": t["adr_real_drift"],
                    "downturn": t.get("adr_downturn", 0.0), "occ_change": t.get("adr_occ_change", 0.0),
                    "occ_level": t.get("adr_occ_level", 0.0),
                    "carry_over": t["adr_carry_over"], "drivers": t.get("adr_drivers", 0.0), "noise": t["adr_noise"],
                    "total": t["adr_total"]},
            "share_at_ceiling": t["share_at_ceiling"],
            "calm": {k[5:]: t[k] for k in t if k.startswith("calm_")},
        })

    # ---------- flags ----------
    flags: list[dict] = []
    for b in bridge:
        if b["share_at_ceiling"] >= CEILING_FLAG_SHARE:
            flags.append({"level": "warn", "year": b["year"], "text":
                          f"In {b['year']}, {b['share_at_ceiling']:.0%} of paths are at the occupancy ceiling ({ceiling:.1%}). "
                          "The model caps demand there; in reality prices and new supply would respond, so occupancy growth "
                          "near zero with a narrow band in those years reflects the cap, not a forecast of stability."})
            break
    # Judge supply over the years where the pipeline no longer covers the outlook (third forecast year on).
    late = bridge[2:] or bridge
    fut_supply = float(np.mean([b["supply"]["total"] for b in late]))
    if hist_supply_growth is not None and np.log1p(hist_supply_growth) - fut_supply > SUPPLY_GAP_FLAG:
        flags.append({"level": "warn", "year": None, "text":
                      f"Supply grows {pct(np.expm1(fut_supply))} a year on average from {late[0]['year']} against "
                      f"{pct(hist_supply_growth)} historically. The demand trend was earned alongside that historical supply "
                      "growth, so occupancy rises if future supply is understated."})
    for pos in positions:
        for k, label in (("occ_growth", "Occupancy growth"), ("adr_growth", "ADR growth"), ("revpar_growth", "RevPAR growth")):
            if pos[k]["outside_p5_p95"]:
                flags.append({"level": "warn", "year": pos["year"], "text":
                              f"{label} P50 in {pos['year']} is outside the market's historical 5th to 95th percentile range."})
        if pos["occ_above_hist_max"]:
            flags.append({"level": "warn", "year": pos["year"], "text":
                          f"Occupancy P50 in {pos['year']} is above the highest annual occupancy in the history "
                          f"({hist.occ_annual_max:.1%})."})
    if cpi_path:
        run = 0
        for r in summary[1:]:
            real = (1 + r["adr_growth"]["p50"]) / (1 + (cpi_path.get(str(r["year"])) or 0)) - 1
            run = run + 1 if real > 0.03 else 0
            if run == 3:
                flags.append({"level": "warn", "year": r["year"], "text":
                              "Real ADR growth is above 3% at P50 for three years in a row."})
                break

    # 5-year cumulative RevPAR against history
    cum = {}
    win = _windows(hist.annual, "revpar", "use_adr")
    if len(win) >= 3 and len(years) >= 6:
        f5 = paths.revpar[:, 5] / paths.revpar[:, 0] - 1
        p50 = float(np.percentile(f5, 50))
        cum = {"forecast_p50": p50, "forecast_p10": float(np.percentile(f5, 10)), "forecast_p90": float(np.percentile(f5, 90)),
               "history_n": int(len(win)), "history_p10": float(np.percentile(win, 10)), "history_p50": float(np.percentile(win, 50)),
               "history_p90": float(np.percentile(win, 90)), "percentile": float((win <= p50).mean()),
               "from_year": years[0], "to_year": years[5]}
        if p50 > cum["history_p90"]:
            flags.append({"level": "warn", "year": None, "text":
                          f"Cumulative RevPAR growth {years[0]} to {years[5]} at P50 ({pct(p50)}) is above the 90th percentile "
                          f"of the market's own five-year periods ({pct(cum['history_p90'])})."})

    # ---------- reconciliation: long-run baseline to forecast (mean log growth, exact) ----------
    recon = []
    if anchor is not None:
        drag0 = shock_drag0 if shock_drag0 is not None else dm.expected_shock_drag()
        d_lr, s_lr, adr_lr = anchor.log("demand"), anchor.log("supply"), anchor.log("adr")
        cpi_lr = float(np.log1p(anchor.cpi_mean)) if (cpi_path and anchor.cpi_mean is not None) else 0.0
        for b in bridge:
            d, s_, a = b["demand"], b["supply"], b["adr"]
            row = {
                "year": b["year"],
                "baseline": float(np.log1p(anchor.revpar)),
                "supply_vs_trend": -(s_["total"] - s_lr) + d["new_hotels"],
                "pricing_response": a["downturn"] + a["occ_change"] + a["occ_level"],
                "inflation_vs_history": (a["inflation"] - cpi_lr) if cpi_path else 0.0,
                "shocks_vs_average": d["shocks"] - drag0,
                "momentum": d["momentum"] + a["carry_over"],
                "drivers": d.get("drivers", 0.0) + a.get("drivers", 0.0),
                "ceiling": d["ceiling"],
                "trend_adjustments": (d["trend"] - (d_lr - drag0)) + (a["real_drift"] - (adr_lr - cpi_lr)),
                "noise": d["noise"] + a["noise"],
            }
            row["total"] = d["total"] - s_["total"] + a["total"]
            recon.append(row)

    # ---------- sentences ----------
    shock_drag = dm.expected_shock_drag()
    ny = dm.normal_years
    if anchor is not None and basis == "long_run_cagr":
        trend_line = (f"Baseline: the market's long-run CAGR from {anchor.start} to {anchor.end}. RevPAR grew {pct(anchor.revpar)} a "
                      f"year: occupancy {pct(anchor.occupancy)} and ADR {pct(anchor.adr)}"
                      + (f" (real ADR {pct(anchor.adr_real)} after average inflation of {pct(anchor.cpi_mean)})" if anchor.adr_real is not None and cpi_path else "")
                      + f". Demand grew {pct(anchor.demand)} a year against supply {pct(anchor.supply)}. The demand trend "
                      f"({pct(np.expm1(dm.mu))}) is the long-run demand CAGR plus {pp(-shock_drag)} points to offset the expected "
                      "drag of shocks, so the average path grows at the long-run rate while individual paths still carry downturns.")
    else:
        trend_line = (f"Demand trend {pct(np.expm1(dm.mu))} a year: the average growth in {len(ny)} normal years "
                      f"({min(ny)} to {max(ny)}), shrunk toward the prior with weight {dm.w_mu:.2f} on the data. Shock and recovery "
                      f"years ({', '.join(map(str, dm.shock_years)) or 'none'}) are modelled separately; their expected drag is "
                      f"about {pp(shock_drag)} points of growth a year.")
    st = supply_trend if supply_trend is not None else sp.unannounced_rate
    drivers = [
        trend_line,
        f"Momentum: {dm.phi:.2f} of the gap between last year's growth and the trend carries into the next year.",
        (f"Supply: never below its long-run trend of {pct(st)} a year from today's level. The pipeline drives supply above "
         "that trend; where it falls short, unannounced hotels fill the gap ("
         + ", ".join(f"{y} {v * 100:+.1f}%" for y, v in sp.unannounced_by_year.items() if y > years[0])
         + " of today's rooms). Only pipeline rooms above the trend bring extra demand.")
        if st is not None else "Supply: the known pipeline and reopenings plus existing stock less removals.",
        f"ADR: {'inflation (' + ', '.join(f'{y} {v * 100:.1f}%' for y, v in cpi_path.items()) + ') plus ' if cpi_path else ''}"
        f"a {'real ' if cpi_path else ''}drift of {pct(am.alpha)} a year, adjusted by the pricing rule. In normal years rate "
        f"moves {am.beta_change:+.2f}% for each 1% occupancy change beyond its trend (inverse: more occupancy, less rate "
        f"growth). Rate growth falls {abs(am.beta_level) * 100:.0f} basis points for each point that occupancy sat above "
        f"its normal {am.occ_ref:.1%} the year before, and a further {abs(am.beta_high) * 100:.0f} for each point above "
        f"{am.high_threshold:.0%}. Downturns pass through to rate at {am.beta:.2f}% per 1% fall in occupancy.",
    ]
    per_year = []
    for b, r in zip(bridge, summary[1:]):
        d, s_, a, c = b["demand"], b["supply"], b["adr"], b["calm"]
        parts_s = [("existing stock", s_["existing_level"]), ("removals", s_["removals"]), ("new builds", s_["new_builds"]),
                   ("reopenings", s_["reopenings"]), ("unannounced", s_["unannounced"])]
        main_s = ", ".join(f"{n} {pp(v)}" for n, v in parts_s if abs(v) >= 0.0005) or "no change"
        if c:
            parts_d = [("trend", d["trend"]), ("momentum", c["demand_momentum"]), ("drivers", c.get("demand_drivers", 0.0)),
                       ("demand from new and reopened hotels", c["demand_new_hotels"]), ("occupancy ceiling", c["demand_ceiling"])]
            main_d = ", ".join(f"{n} {pp(v)}" for n, v in parts_d if abs(v) >= 0.0005)
            text = (f"{b['year']}: occupancy {pct(r['occ_growth']['p50'])} and ADR {pct(r['adr_growth']['p50'])} at P50. "
                    f"In the {c['share']:.0%} of paths with no shock that year, demand grows {pp(c['demand_total'])} points "
                    f"({main_d}) against supply {pp(c['supply_total'])} points ({main_s}), so occupancy moves "
                    f"{pp(c['occ_total'])} points; ADR moves {pp(c['adr_total'])} points (inflation {pp(a['inflation'])}, "
                    f"real drift {pp(a['real_drift'])}, pricing rule {pp(c['adr_pricing_power'])}"
                    + (f", drivers {pp(c.get('adr_drivers', 0.0))}" if abs(c.get('adr_drivers', 0.0)) >= 0.0005 else "") + "). "
                    f"Shocks in the other paths pull mean demand growth to {pp(d['total'])} points.")
        else:
            text = (f"{b['year']}: occupancy {pct(r['occ_growth']['p50'])} and ADR {pct(r['adr_growth']['p50'])} at P50. "
                    f"Demand {pp(d['total'])} points against supply {pp(s_['total'])} points ({main_s}).")
        per_year.append({"year": b["year"], "text": text})
    stories = year_stories(paths, summary, bridge, recon, flags, sp, anchor, cpi_path, stub_ytd, stub_info, cpi_sources)
    return {"bridge": bridge, "flags": flags, "drivers": drivers, "per_year": per_year, "cumulative_revpar": cum,
            "reconciliation": recon, "years": stories,
            "historical_supply_growth": hist_supply_growth, "unannounced_rate": sp.unannounced_rate,
            "unannounced_by_year": {str(k): v for k, v in sp.unannounced_by_year.items()},
            "seasonal_index": sp.seasonal_index,
            "note": "The bridge splits the mean log growth (in points, about equal to percent) into components that add up "
                    "exactly. The mean includes rare shocks, which pull it below the median (P50). The rows for years "
                    "without a shock describe the typical path that the P50 reflects."}


# ---------------------------------------------------------------------------
# Year-by-year stories: plain-language explanation of why each year sits where it does.
# Every number comes from the simulation output passed in.
# ---------------------------------------------------------------------------

RECON_LABELS = {
    "supply_vs_trend": "supply growing {dir} its long-run trend",
    "pricing_response": "the ADR response to occupancy",
    "inflation_vs_history": "inflation {dir} its historical average",
    "shocks_vs_average": "downturn risk {shockdir}",
    "momentum": "momentum from the year before",
    "drivers": "forward-looking drivers",
    "ceiling": "the occupancy ceiling",
    "trend_adjustments": "adjustments to the trend",
}


def _f(x, dp=1):
    return f"{x * 100:+.{dp}f}%"


def _pts(x, dp=1):
    return f"{x * 100:+.{dp}f} points"


def _openings(sp: SupplyPaths, year: int) -> list[str]:
    from engine.supply.pipeline import annual_open_probabilities
    if sp.projects is None or sp.projects.empty:
        return []
    probs = annual_open_probabilities(sp).set_index("project_id")
    out = []
    for r in sp.projects.itertuples():
        if r.project_id not in probs.index:
            continue
        by = probs.loc[r.project_id]
        p_now = float(by.get(str(year), 0.0)) - float(by.get(str(year - 1), 0.0))
        if p_now >= 0.25 and r.rooms >= 0.005 * sp.base_rooms:
            kind = "reopening of " if r.project_type == "renovation_reopening" else ""
            out.append((r.rooms, f"{kind}{r.name} ({r.rooms:.0f} rooms, {p_now:.0%} chance of opening this year)"))
    return [t for _, t in sorted(out, reverse=True)[:4]]


def year_stories(paths: Paths, summary: list[dict], bridge: list[dict], recon: list[dict], flags: list[dict],
                 sp: SupplyPaths, anchor, cpi_path: dict | None, stub_ytd: dict | None, stub_info: dict | None,
                 cpi_sources: dict | None = None) -> list[dict]:
    stories = []
    # Stub year
    r0 = summary[0]
    if stub_ytd:
        lines = [f"{stub_ytd['months']} months are actual. Year to date, occupancy moved {_f(stub_ytd['occ'])}, ADR "
                 f"{_f(stub_ytd['adr'])} and RevPAR {_f(stub_ytd['revpar'])} against the same months last year."]
        if stub_info:
            lines.append(f"The remaining {12 - stub_ytd['months']} months follow last year's same months, moved by the recent "
                         f"year-to-date trend (demand {_f(stub_info['trend_demand'])}, ADR {_f(stub_info['trend_adr'])}), "
                         "with errors sized on how that rule did in past years.")
        stories.append({"year": r0["year"], "label": r0["label"],
                        "headline": f"RevPAR {_f(r0['revpar_growth']['p50'])}: occupancy {_f(r0['occ_growth']['p50'])}, "
                                    f"ADR {_f(r0['adr_growth']['p50'])} (P50).",
                        "lines": lines, "flags": []})
    lr = anchor.log("revpar") if anchor is not None else None
    for b, r, rc in zip(bridge, summary[1:], recon or [None] * len(bridge)):
        y = b["year"]
        c = b.get("calm") or {}
        d, s, a = b["demand"], b["supply"], b["adr"]
        dem = r["demand_growth"]["p50"]
        sup = r["supply_growth"]["p50"]
        # Occupancy
        dparts = [f"trend {_pts(d['trend'])}"]
        for key, label in (("demand_momentum", "momentum"), ("demand_new_hotels", "demand brought by new hotels"),
                           ("demand_drivers", "drivers"), ("demand_ceiling", "occupancy ceiling")):
            v = c.get(key, 0.0)
            if abs(v) >= 0.001:
                dparts.append(f"{label} {_pts(v)}")
        sparts = []
        named = _openings(sp, y)
        if named:
            sparts.append("openings: " + "; ".join(named))
        if abs(s.get("unannounced", 0)) >= 0.001:
            sparts.append(f"{_pts(s['unannounced'])} of hotels not yet in the pipeline, keeping supply on its long-run trend")
        if abs(s.get("existing_level", 0)) >= 0.003:
            sparts.append(f"{_pts(s['existing_level'])} from existing hotels' availability")
        if abs(s.get("removals", 0)) >= 0.001:
            sparts.append(f"{_pts(s['removals'])} closures and conversions")
        if abs(s.get("drivers", 0)) >= 0.001:
            sparts.append(f"{_pts(s['drivers'])} supply drivers")
        occ_line = (f"Occupancy {_f(r['occ_growth']['p50'])}: demand grows {_f(dem)} against supply {_f(sup)} (P50). "
                    f"Demand is made up mainly of {', '.join(dparts)}."
                    + (f" Supply comes from {'; '.join(sparts)}." if sparts else ""))
        # ADR
        aparts = []
        if cpi_path:
            src = (cpi_sources or {}).get(str(y))
            aparts.append(f"inflation {_pts(a['inflation'])}" + (f" ({src})" if src else ""))
        aparts.append(f"{'real ' if cpi_path else ''}long-run trend {_pts(a['real_drift'])}")
        chg = c.get("adr_occ_change", a.get("occ_change", 0.0))
        lvl = c.get("adr_occ_level", a.get("occ_level", 0.0))  # calm paths: no downturn pass-through
        if abs(chg) >= 0.001:
            aparts.append(f"{'giving back' if chg < 0 else 'adding'} {abs(chg) * 100:.1f} points as occupancy "
                          f"{'rises' if chg < 0 else 'falls'} (occupancy and rate trade off)")
        if abs(lvl) >= 0.001:
            occ_prev = summary[summary.index(r) - 1]["occupancy"]["p50"]
            aparts.append(f"{_pts(lvl)} because occupancy ran at {occ_prev:.1%} last year, "
                          f"{'above' if lvl < 0 else 'below'} the market's normal level")
        if abs(c.get("adr_drivers", a.get("drivers", 0))) >= 0.001:
            aparts.append(f"drivers {_pts(c.get('adr_drivers', a.get('drivers', 0)))}")
        shown = ((a["inflation"] if cpi_path else 0.0) + a["real_drift"] + chg + lvl
                 + c.get("adr_drivers", a.get("drivers", 0)))
        rest = float(np.log1p(r["adr_growth"]["p50"])) - shown
        if abs(rest) >= 0.001:
            aparts.append(f"{_pts(rest)} from the rest (downturns in some paths, carry-over from last year and noise)")
        adr_line = f"ADR {_f(r['adr_growth']['p50'])}: {', '.join(aparts)}."
        lines_rev = (f"RevPAR {_f(r['revpar_growth']['p50'])} = occupancy {_f(r['occ_growth']['p50'])} combined with ADR "
                     f"{_f(r['adr_growth']['p50'])}.")
        lines = [occ_line, adr_line, lines_rev]
        # Against the long-run baseline (mean)
        if rc is not None and lr is not None:
            diff = rc["total"] - lr
            items = []
            for k in RECON_LABELS:
                v = rc.get(k, 0.0)
                if abs(v) >= 0.002:
                    lab = RECON_LABELS[k].format(dir="above" if (k == "supply_vs_trend" and v < 0) or (k == "inflation_vs_history" and v > 0) else "below",
                                                 shockdir="weighing on this year" if v < 0 else "easing as past downturns recover")
                    items.append((abs(v), f"{lab} ({_pts(v)})"))
            items.sort(reverse=True)
            why = "; ".join(t for _, t in items[:2]) if items else "no single reason stands out"
            lines.append(f"Against the long-run RevPAR growth of {_f(np.expm1(lr))} a year, the average path is "
                         f"{abs(diff) * 100:.1f} points {'higher' if diff > 0 else 'lower'}, mainly because of {why}.")
        lines.append(f"Range: RevPAR {_f(r['revpar_growth']['p10'])} to {_f(r['revpar_growth']['p90'])} (P10 to P90); "
                     f"{r['p_revpar_negative']:.0%} chance RevPAR falls.")
        stories.append({"year": y, "label": r["label"],
                        "headline": f"RevPAR {_f(r['revpar_growth']['p50'])}: occupancy {_f(r['occ_growth']['p50'])}, "
                                    f"ADR {_f(r['adr_growth']['p50'])} (P50).",
                        "lines": lines, "flags": [f["text"] for f in flags if f.get("year") == y]})
    return stories
