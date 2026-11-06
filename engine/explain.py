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
                      cpi_path: dict | None) -> dict:
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
            "adr": {"inflation": t["adr_inflation"], "real_drift": t["adr_real_drift"], "pricing_power": t["adr_pricing_power"],
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

    # ---------- sentences ----------
    shock_drag = dm.p_mean("moderate") * np.mean([sum(p.deviations) for p in dm.shock_profiles if p.cls == "moderate"] or [0]) + \
        dm.p_mean("severe") * np.mean([sum(p.deviations) for p in dm.shock_profiles if p.cls == "severe"] or [0])
    ny = dm.normal_years
    drivers = [
        f"Demand trend {pct(np.expm1(dm.mu))} a year: the average growth in {len(ny)} normal years "
        f"({min(ny)} to {max(ny)}), shrunk toward the prior with weight {dm.w_mu:.2f} on the data. Shock and recovery years "
        f"({', '.join(map(str, dm.shock_years)) or 'none'}) are modelled separately; their expected drag is about "
        f"{pp(shock_drag)} points of growth a year.",
        f"Momentum: {dm.phi:.2f} of the gap between last year's growth and the trend carries into the next year.",
        f"Supply: the known pipeline and reopenings, existing stock less removals, and unannounced hotels that fill the gap "
        f"between the market's median historical supply growth ({pct(sp.unannounced_rate)} a year; half in the second forecast "
        "year, all from the third) and what the pipeline delivers: "
        + ", ".join(f"{y} {v * 100:+.1f}%" for y, v in sp.unannounced_by_year.items() if y > years[0]) + "."
        if sp.unannounced_rate > 0 else
        "Supply: the known pipeline and reopenings plus existing stock less removals. No unannounced supply is added "
        "because the market's historical supply growth is zero or negative.",
        f"ADR: {'inflation (' + ', '.join(f'{y} {v * 100:.1f}%' for y, v in cpi_path.items()) + ') plus ' if cpi_path else ''}"
        f"a real drift of {pct(am.alpha)} a year and pricing power of {am.beta:.2f} (ADR moves {am.beta:.2f}% for each 1% "
        "change in occupancy).",
    ]
    per_year = []
    for b, r in zip(bridge, summary[1:]):
        d, s_, a, c = b["demand"], b["supply"], b["adr"], b["calm"]
        parts_s = [("existing stock", s_["existing_level"]), ("removals", s_["removals"]), ("new builds", s_["new_builds"]),
                   ("reopenings", s_["reopenings"]), ("unannounced", s_["unannounced"])]
        main_s = ", ".join(f"{n} {pp(v)}" for n, v in parts_s if abs(v) >= 0.0005) or "no change"
        if c:
            parts_d = [("trend", d["trend"]), ("momentum", c["demand_momentum"]),
                       ("demand from new and reopened hotels", c["demand_new_hotels"]), ("occupancy ceiling", c["demand_ceiling"])]
            main_d = ", ".join(f"{n} {pp(v)}" for n, v in parts_d if abs(v) >= 0.0005)
            text = (f"{b['year']}: occupancy {pct(r['occ_growth']['p50'])} and ADR {pct(r['adr_growth']['p50'])} at P50. "
                    f"In the {c['share']:.0%} of paths with no shock that year, demand grows {pp(c['demand_total'])} points "
                    f"({main_d}) against supply {pp(c['supply_total'])} points ({main_s}), so occupancy moves "
                    f"{pp(c['occ_total'])} points; ADR moves {pp(c['adr_total'])} points (inflation {pp(a['inflation'])}, "
                    f"real drift {pp(a['real_drift'])}, pricing power {pp(c['adr_pricing_power'])}). "
                    f"Shocks in the other paths pull mean demand growth to {pp(d['total'])} points.")
        else:
            text = (f"{b['year']}: occupancy {pct(r['occ_growth']['p50'])} and ADR {pct(r['adr_growth']['p50'])} at P50. "
                    f"Demand {pp(d['total'])} points against supply {pp(s_['total'])} points ({main_s}).")
        per_year.append({"year": b["year"], "text": text})
    return {"bridge": bridge, "flags": flags, "drivers": drivers, "per_year": per_year, "cumulative_revpar": cum,
            "historical_supply_growth": hist_supply_growth, "unannounced_rate": sp.unannounced_rate,
            "unannounced_by_year": {str(k): v for k, v in sp.unannounced_by_year.items()},
            "seasonal_index": sp.seasonal_index,
            "note": "The bridge splits the mean log growth (in points, about equal to percent) into components that add up "
                    "exactly. The mean includes rare shocks, which pull it below the median (P50). The rows for years "
                    "without a shock describe the typical path that the P50 reflects."}
