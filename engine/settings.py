"""User settings: the adjustable weights and biases behind a forecast.

settings_schema() lists every adjustable value with its default (from config)
so the web page can render the Assumptions tab. apply_settings() turns a dict of
user changes into modified inputs plus a list of the adjustments, which every
output reports alongside the run.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from engine.config import CONFIG_DIR, load_yaml

STAGES = [("under_construction", "Under construction"), ("under_renovation", "Renovation reopening (temporarily closed)"),
          ("final_planning", "Final planning"), ("planning", "Planning"), ("proposed", "Proposed"), ("deferred", "Deferred")]


def _tri_mode(spec: dict) -> float:
    return float(spec["mode"])


def _adr_priors() -> dict:
    try:
        return load_yaml(CONFIG_DIR / "priors.yaml").get("adr", {})
    except FileNotFoundError:
        return {}


def settings_schema(ppri: dict, macro: dict, driver_priors: dict, horizon_years: list[int] | None = None) -> list[dict]:
    """Groups of fields. kind: percent (shown x100), number, bool, multiplier, months, points.
    default None means the engine works it out from the data (shown as 'auto')."""
    pa = _adr_priors()
    hi = pa.get("high_occupancy", {"threshold": 0.90, "dampening": -0.5})
    g = []
    g.append({"id": "baseline", "title": "Baseline",
              "help": "The baseline is the market's long-run CAGR (RevPAR = occupancy x ADR; demand = supply + occupancy). "
                      "The alternative fits the trend on normal years only.",
              "fields": [
                  {"key": "anchor.basis", "label": "Trend basis", "kind": "select", "default": "long_run_cagr",
                   "options": [["long_run_cagr", "Long-run CAGR over the whole history"], ["fitted_trend", "Fitted normal-year trend"]]},
                  {"key": "anchor.start_year", "label": "Long-run CAGR from year (blank: first complete year)", "kind": "year",
                   "min": 1990, "max": 2030, "step": 1, "default": None},
              ]})
    g.append({"id": "pipeline", "title": "Pipeline: chance of opening and delay by stage",
              "help": "Probability a project opens within the forecast horizon, and the most likely delay after its projected opening date.",
              "fields": [f for key, label in STAGES for f in (
                  {"key": f"pipeline.{key}.p", "label": f"{label}: chance of opening", "kind": "percent", "min": 0, "max": 1, "step": 0.05,
                   "default": ppri["stages"][key]["p_open_within_horizon"]},
                  {"key": f"pipeline.{key}.delay_mode", "label": f"{label}: most likely delay", "kind": "months", "min": 0, "max": 48, "step": 1,
                   "default": _tri_mode(ppri["stages"][key]["delay_months"])},
              )]})
    g.append({"id": "supply", "title": "Supply and new-hotel demand",
              "help": "Closures, the demand new hotels bring with them, and hotels not yet in the pipeline export.",
              "fields": [
                  {"key": "supply.removal_rate", "label": "Rooms closed or converted each year", "kind": "percent", "min": 0, "max": 0.03, "step": 0.001,
                   "default": _tri_mode(ppri["removals"]["annual_rate"])},
                  {"key": "supply.induced_new", "label": "New hotels: share of their demand that is new to the market", "kind": "percent", "min": 0, "max": 1, "step": 0.05,
                   "default": _tri_mode(ppri["induced_demand"]["share_of_new_rooms"])},
                  {"key": "supply.induced_reopened", "label": "Reopened hotels: share of their former demand that returns", "kind": "percent", "min": 0, "max": 1, "step": 0.05,
                   "default": _tri_mode(ppri["induced_demand"]["share_of_reopened_rooms"])},
                  {"key": "supply.unannounced_enabled", "label": "Keep supply at least on its long-run trend (adds unannounced hotels)", "kind": "bool",
                   "default": bool(ppri.get("unannounced_supply", {}).get("enabled", True))},
                  {"key": "supply.unannounced_rate", "label": "Long-run supply trend per year (blank: market's CAGR)", "kind": "percent", "min": -0.05, "max": 0.10, "step": 0.001,
                   "default": None},
                  {"key": "supply.trend_share_y1", "label": "Share of the supply trend applied in the first full year", "kind": "percent", "min": 0, "max": 1, "step": 0.05,
                   "default": float(ppri.get("unannounced_supply", {}).get("trend_share_by_year", [1.0])[0])},
                  {"key": "supply.trend_share_y2", "label": "Share of the supply trend applied in the second full year", "kind": "percent", "min": 0, "max": 1, "step": 0.05,
                   "default": float((ppri.get("unannounced_supply", {}).get("trend_share_by_year", [1.0, 1.0]) + [1.0])[1])},
              ]})
    g.append({"id": "demand", "title": "Demand: trend and shocks",
              "help": "Biases tilt the fitted trend. Shock multipliers scale the annual chance of a downturn learned from history.",
              "fields": [
                  {"key": "demand.trend_bias", "label": "Demand trend adjustment (points of growth a year)", "kind": "points", "min": -0.05, "max": 0.05, "step": 0.001, "default": 0.0},
                  {"key": "demand.shock_mult_moderate", "label": "Chance of a moderate shock (multiplier)", "kind": "multiplier", "min": 0, "max": 5, "step": 0.1, "default": 1.0},
                  {"key": "demand.shock_mult_severe", "label": "Chance of a severe, COVID-scale shock (multiplier)", "kind": "multiplier", "min": 0, "max": 5, "step": 0.1, "default": 1.0},
              ]})
    g.append({"id": "adr", "title": "ADR and the occupancy trade-off",
              "help": "ADR growth = inflation + real drift + the pricing rule. Downturns pass through to rate. In normal years "
                      "occupancy and rate move inversely, and a high occupancy level dampens rate growth, more steeply above the threshold.",
              "fields": [
                  {"key": "adr.drift_bias", "label": "Real ADR drift adjustment (points a year)", "kind": "points", "min": -0.05, "max": 0.05, "step": 0.001, "default": 0.0},
                  {"key": "adr.occ_change_response", "label": "Normal years: ADR % per 1% occupancy change (negative = inverse)", "kind": "number",
                   "min": -1.5, "max": 1.0, "step": 0.05, "default": float(pa.get("occ_change_response", {}).get("value", 0.0))},
                  {"key": "adr.level_dampening", "label": "Rate dampening per point of occupancy above the market's normal (blank: fitted)", "kind": "number",
                   "min": -2.0, "max": 0.0, "step": 0.05, "default": None},
                  {"key": "adr.high_threshold", "label": "Occupancy above which rate growth is dampened more", "kind": "percent",
                   "min": 0.7, "max": 1.0, "step": 0.005, "default": float(hi["threshold"])},
                  {"key": "adr.high_dampening", "label": "Extra dampening per point of occupancy above that threshold", "kind": "number",
                   "min": -3.0, "max": 0.0, "step": 0.05, "default": float(hi["dampening"])},
                  {"key": "adr.pricing_power", "label": "Downturns: ADR % per 1% occupancy fall or recovery (blank: fitted)", "kind": "number", "min": 0, "max": 3, "step": 0.05, "default": None},
              ]})
    if macro.get("cpi_projections", {}).get("values"):
        proj = {int(v["year"]): float(v["rate"]) for v in macro["cpi_projections"]["values"]}
        yrs = horizon_years or sorted(proj)
        g.append({"id": "inflation", "title": "Inflation path",
                  "help": "Used in CPI mode. Defaults are the sourced projections.",
                  "fields": [{"key": f"inflation.{y}", "label": f"Inflation {y}", "kind": "percent", "min": -0.02, "max": 0.15, "step": 0.001,
                              "default": proj.get(y)} for y in yrs if y in proj]})
    g.append({"id": "uncertainty", "title": "Uncertainty and limits",
              "help": "Band width and the occupancy ceiling.",
              "fields": [
                  {"key": "uncertainty.spread_multiplier", "label": "Band width (multiplier on all years)", "kind": "multiplier", "min": 0.5, "max": 2.5, "step": 0.05, "default": 1.0},
                  {"key": "uncertainty.horizon_widening", "label": "Widen later years as calibrated by the backtest", "kind": "bool", "default": True},
                  {"key": "occupancy.ceiling", "label": "Occupancy ceiling (blank: from monthly peaks)", "kind": "percent", "min": 0.5, "max": 1.0, "step": 0.005, "default": None},
              ]})
    g.append({"id": "drivers", "title": "Drivers",
              "help": "Forward-looking events and risks. Each happens with its probability; its size is drawn from its range.",
              "fields": [{"key": "drivers.include_proposed", "label": "Include proposed drivers not yet approved", "kind": "bool",
                          "default": bool(driver_priors.get("include_proposed", True))}]})
    return g


@dataclass
class Applied:
    ppri: dict
    macro: dict
    driver_priors: dict
    knobs: dict
    adjustments: list[str] = field(default_factory=list)


def _clamp_tri(spec: dict, mode: float) -> dict:
    s = dict(spec)
    s["mode"] = float(mode)
    s["low"] = min(s["low"], s["mode"])
    s["high"] = max(s["high"], s["mode"])
    return s


def apply_settings(settings: dict | None, ppri: dict, macro: dict, driver_priors: dict) -> Applied:
    s = settings or {}
    ppri = copy.deepcopy(ppri)
    macro = copy.deepcopy(macro)
    driver_priors = copy.deepcopy(driver_priors)
    adj: list[str] = []
    labels = {f["key"]: f for grp in settings_schema(ppri, macro, driver_priors) for f in grp["fields"]}

    def fmt(key, v):
        f = labels.get(key, {})
        if v is None:
            return "auto"
        if isinstance(v, bool):
            return "on" if v else "off"
        k = f.get("kind")
        if k == "percent":
            return f"{v * 100:.1f}%"
        if k == "points":
            return f"{v * 100:+.1f} points"
        if k == "multiplier":
            return f"x{v:g}"
        if k == "months":
            return f"{v:g} months"
        if k == "select":
            return dict(f.get("options", [])).get(v, str(v))
        if k == "year":
            return str(int(v))
        return f"{v:g}"

    def changed(key) -> bool:
        if key not in s or s[key] is None and labels.get(key, {}).get("default") is None:
            return False
        return s[key] != labels.get(key, {}).get("default")

    for key, v in s.items():
        if key in labels and changed(key):
            adj.append(f"{labels[key]['label']}: {fmt(key, labels[key]['default'])} -> {fmt(key, v)}")

    for key, _ in STAGES:
        if changed(f"pipeline.{key}.p"):
            ppri["stages"][key]["p_open_within_horizon"] = float(s[f"pipeline.{key}.p"])
        if changed(f"pipeline.{key}.delay_mode"):
            ppri["stages"][key]["delay_months"] = _clamp_tri(ppri["stages"][key]["delay_months"], s[f"pipeline.{key}.delay_mode"])
    if changed("supply.removal_rate"):
        ppri["removals"]["annual_rate"] = _clamp_tri(ppri["removals"]["annual_rate"], s["supply.removal_rate"])
    if changed("supply.induced_new"):
        ppri["induced_demand"]["share_of_new_rooms"] = _clamp_tri(ppri["induced_demand"]["share_of_new_rooms"], s["supply.induced_new"])
    if changed("supply.induced_reopened"):
        ppri["induced_demand"]["share_of_reopened_rooms"] = _clamp_tri(ppri["induced_demand"]["share_of_reopened_rooms"], s["supply.induced_reopened"])
    for i, key in ((0, "supply.trend_share_y1"), (1, "supply.trend_share_y2")):
        if changed(key):
            ts = list(ppri.setdefault("unannounced_supply", {}).get("trend_share_by_year", [1.0] * 5))
            ts[i] = float(s[key])
            ppri["unannounced_supply"]["trend_share_by_year"] = ts
    if changed("supply.unannounced_enabled"):
        ppri.setdefault("unannounced_supply", {})["enabled"] = bool(s["supply.unannounced_enabled"])
    if "drivers.include_proposed" in s:
        driver_priors["include_proposed"] = bool(s["drivers.include_proposed"])
    infl = {int(k.split(".")[1]): float(v) for k, v in s.items() if k.startswith("inflation.") and changed(k)}
    if infl and macro.get("cpi_projections", {}).get("values"):
        for item in macro["cpi_projections"]["values"]:
            if int(item["year"]) in infl:
                item["rate"] = infl[int(item["year"])]
    knobs = {
        "trend_bias": float(s.get("demand.trend_bias") or 0.0),
        "drift_bias": float(s.get("adr.drift_bias") or 0.0),
        "pricing_power": s.get("adr.pricing_power"),
        "occ_change_response": s.get("adr.occ_change_response"),
        "level_dampening": s.get("adr.level_dampening"),
        "high_threshold": s.get("adr.high_threshold"),
        "high_dampening": s.get("adr.high_dampening"),
        "shock_mult_moderate": float(s.get("demand.shock_mult_moderate", 1.0) if s.get("demand.shock_mult_moderate") is not None else 1.0),
        "shock_mult_severe": float(s.get("demand.shock_mult_severe", 1.0) if s.get("demand.shock_mult_severe") is not None else 1.0),
        "spread_multiplier": float(s.get("uncertainty.spread_multiplier") or 1.0),
        "horizon_widening": bool(s.get("uncertainty.horizon_widening", True)),
        "ceiling": s.get("occupancy.ceiling"),
        "unannounced_rate": s.get("supply.unannounced_rate"),
        "anchor_basis": s.get("anchor.basis") or "long_run_cagr",
        "anchor_start": int(s["anchor.start_year"]) if s.get("anchor.start_year") not in (None, "") else None,
    }
    return Applied(ppri, macro, driver_priors, knobs, adj)
