"""Ledger forecast engine.

For each year the forecast is a list of lines, each moving occupancy growth or ADR growth by a stated
number of points, with a sentence explaining it and the basis of the number (fitted on the European
panel, a labelled assumption, a user decision, or a sourced driver):

  Layer 1  Baseline: the market's long-run RevPAR CAGR (occupancy flat by default, rate carries it),
           plus inflation against its historical average.
  Layer 2  Supply: growth above the long-run supply trend (volume), the tier and product mix of the
           rooms that open and close (quality), and how directly they compete (overlap).
  Layer 3  Drivers: events, air access, source markets and FX, policy, competing supply, and
           geopolitical risk, sized by calculators and weighted by their probability.

The central forecast is the sum of the lines; RevPAR = (1 + occupancy) x (1 + ADR) - 1. A simulation
is used only for the range: the historical miss of the CAGR rule across European submarkets (scaled to
this market's volatility), plus the uncertainty of the pipeline and of each driver.
"""

from __future__ import annotations

import math
import re
import time
import zlib

import numpy as np
import pandas as pd

from engine import __version__
from engine.config import MarketConfig
from engine.drivers.apply import profile
from engine.drivers.schema import Magnitude
from engine.drivers.validate import check_cards, parse_cards, resolve_probability
from engine.forecast import cpi_history, cpi_rate, load_driver_priors
from engine.history.analysis import analyse_history
from engine.history.anchor import anchor_window, compute_anchor, first_clean_year
from engine.ingest.core import IngestResult
from engine.ledger import calculators as C
from engine.ledger.registry import TIER_LABEL, TIERS, effective, tier_of
from engine.settings import apply_settings
from engine.supply.pipeline import simulate_supply

Z_LEVELS = {5: -1.6449, 10: -1.2816, 25: -0.6745, 50: 0.0, 75: 0.6745, 90: 1.2816, 95: 1.6449}
MONTHS = C.MONTHS


def _pct(x, dp=1):
    return f"{x * 100:+.{dp}f}%"


def _pts(x, dp=1):
    return f"{x * 100:+.{dp}f} points"


def _line(id_, layer, title, occ=0.0, adr=0.0, text="", basis="", evidence=""):
    return {"id": id_, "layer": layer, "title": title, "occ": float(occ), "adr": float(adr), "text": text,
            "basis": basis, "evidence": evidence}


def _product(name: str) -> str | None:
    n = (name or "").lower()
    if re.search(r"hostel", n):
        return "hostel"
    if re.search(r"apart|serviced|residence", n):
        return "aparthotel"
    return None


def _robust_sd(x: pd.Series) -> float:
    x = x.dropna()
    return float((x.quantile(0.75) - x.quantile(0.25)) / 1.349) if len(x) >= 4 else float("nan")


def _q_from_z(z: np.ndarray, qs: list[int], vals: list[float]) -> np.ndarray:
    zl = np.array([Z_LEVELS[q] for q in qs])
    v = np.asarray(vals, float)
    out = np.interp(z, zl, v)
    lo_s = (v[1] - v[0]) / (zl[1] - zl[0])
    hi_s = (v[-1] - v[-2]) / (zl[-1] - zl[-2])
    out = np.where(z < zl[0], v[0] + (z - zl[0]) * lo_s, out)
    out = np.where(z > zl[-1], v[-1] + (z - zl[-1]) * hi_s, out)
    return out - float(np.interp(0.0, zl, v))          # centred: the median miss is not a forecast


def _seasonal_shares(monthly: pd.DataFrame, covid: set[int]) -> tuple[dict, dict]:
    m = monthly.copy()
    m["y"], m["mo"] = m["period_start"].dt.year, m["period_start"].dt.month
    full = m.groupby("y")["mo"].count()
    yrs = [y for y in full.index if full[y] == 12 and y not in covid][-3:]
    s = m[m["y"].isin(yrs)]
    if s.empty:
        return {k: 1 / 12 for k in range(1, 13)}, {k: 1 / 12 for k in range(1, 13)}
    d = s.groupby("mo")["demand"].sum()
    r = s.groupby("mo")["revenue"].sum()
    return {int(k): float(v / d.sum()) for k, v in d.items()}, {int(k): float(v / r.sum()) for k, v in r.items()}


def run_ledger(res: IngestResult, cfg: MarketConfig, ppri: dict, macro: dict, drivers: list[dict] | None = None,
               settings: dict | None = None, driver_priors: dict | None = None, n_paths: int = 4000,
               seed: int | None = None, registry: dict | None = None) -> dict:
    t0 = time.time()
    seed = cfg.simulation.seed if seed is None else seed
    driver_priors = driver_priors if driver_priors is not None else load_driver_priors()
    applied = apply_settings(settings, ppri, macro, driver_priors)
    ppri, macro, driver_priors, knobs = applied.ppri, applied.macro, applied.driver_priors, applied.knobs
    R = effective(settings, registry)
    adjustments = [a for a in applied.adjustments] + R.adjustments
    covid = set(cfg.estimation.covid_years)
    cpi = cpi_history(macro)
    hist = analyse_history(res.monthly_spliced, res.growth_sample, cfg, cpi)
    ha = hist.annual
    gaps: list[str] = []

    # ---------------- Layer 1: baseline ----------------
    clean = first_clean_year(res.breaks)
    start = R.get("baseline.start_year")
    last_full = int(ha["year"].max())
    win = anchor_window(list(ha["year"]), last_full, covid, None if start in (None, "") else int(start),
                        None if start in (None, "") else last_full - int(start), clean)
    anchor = compute_anchor(ha, win[0], win[1], cpi) if win else None
    if anchor is None:
        raise ValueError("Not enough complete years outside COVID for a long-run CAGR (at least 5 are needed).")
    split = R["baseline.occupancy_split"]
    base_occ_g = 0.0 if split == "flat" else anchor.occupancy
    base_adr_g = (1 + anchor.revpar) / (1 + base_occ_g) - 1

    m = res.monthly.sort_values("period_start").copy()
    last = m["period_start"].max()
    y0, cutoff = int(last.year), int(last.month)
    stub = cutoff < 12
    H = cfg.horizon_years
    full_years = [y0 + i for i in range(1, H + 1)] if stub else [y0 + i for i in range(1, H + 1)]
    years = ([y0] if stub else []) + full_years
    base_year = y0 - 1 if stub else y0
    act = res.annual.set_index("year")
    if base_year not in act.index or not bool(act.loc[base_year, "complete"]):
        raise ValueError(f"The base year {base_year} is not a complete year in the data.")
    b = act.loc[base_year]
    base = {"occ": float(b["occupancy"]), "adr": float(b["adr"]), "supply": float(b["supply"]), "demand": float(b["demand"])}
    dshare, rshare = _seasonal_shares(m, covid)
    rest_share = (12 - cutoff) / 12 if stub else 0.0

    # ---------------- Supply simulation (pipeline, removals, trend floor) ----------------
    months = pd.date_range(last + pd.DateOffset(months=1), pd.Timestamp(year=y0 + H, month=12, day=1), freq="MS")
    trend = float(knobs["unannounced_rate"]) if knobs.get("unannounced_rate") is not None else anchor.supply
    rng = np.random.default_rng(seed)
    sp = simulate_supply(res.pipeline, m, months, ppri, n_paths, rng, trend, sorted(covid))
    month_year = np.array([d.year for d in months])
    # Supply floor in inventory terms (user decision 2026-11-06): the hotel stock never grows below its
    # long-run trend. Unannounced rooms fill any gap between that trend and stock plus the pipeline.
    # Reopenings of temporarily closed hotels return rooms to available supply but not to inventory.
    m["y"] = m["period_start"].dt.year
    has_inv = "inventory_rooms" in m and m["inventory_rooms"].notna().any()
    inv_y = m.groupby("y")["inventory_rooms"].mean() if has_inv else (m.groupby("y")["available_rooms"].mean())
    inv_last = float(m["inventory_rooms"].dropna().iloc[-1]) if has_inv else sp.base_rooms
    inv_win = inv_y[(inv_y.index >= anchor.start) & (inv_y.index <= anchor.end)].dropna()
    inv_trend = float((inv_win.iloc[-1] / inv_win.iloc[0]) ** (1 / (inv_win.index[-1] - inv_win.index[0])) - 1) \
        if len(inv_win) >= 5 and inv_win.index[-1] > inv_win.index[0] else trend
    if knobs.get("unannounced_rate") is not None:
        inv_trend = float(knobs["unannounced_rate"])
    seas_m = np.array([sp.seasonal_index.get(int(d.month), 1.0) for d in months])
    comp_ = sp.components
    inv_core = inv_last + (comp_["new_builds"] - comp_["removed"]) / seas_m[None, :]
    floor_on = bool(ppri.get("unannounced_supply", {}).get("enabled", True))
    t_years = (np.arange(len(months)) + 1) / 12.0
    trend_path = inv_last * (1 + inv_trend) ** t_years
    unann_inv = np.maximum(0.0, trend_path[None, :] - inv_core) if floor_on else np.zeros_like(inv_core)
    inv_m = inv_core + unann_inv
    avail = sp.rooms - comp_["unannounced"] + unann_inv * seas_m[None, :]
    S = np.zeros((n_paths, len(years)))
    for j, y in enumerate(years):
        idx = month_year == y
        S[:, j] = (avail[:, idx] * sp.days[idx][None, :]).sum(axis=1)
        if stub and y == y0:
            S[:, j] += float(m.loc[m["y"] == y0, "supply"].sum())

    # ---------------- Layer 3 inputs: drivers and calculators ----------------
    ctx = C.MarketContext(base["demand"], dshare, rshare, base["adr"], cfg.currency)
    cards, driver_issues = parse_cards(drivers or [])
    calc_out: dict[str, C.CalcResult] = {}
    for c in cards:
        if c.calculator is None:
            continue
        try:
            r = C.compute(c.calculator.model_dump(), ctx, R, c.id)
        except Exception as exc:  # noqa: BLE001
            driver_issues.append({"id": c.id, "level": "error", "text": f"Calculator failed: {exc}"})
            continue
        calc_out[c.id] = r
        prim = r.adr if c.affects == "adr" else r.demand
        lo, mid, hi = sorted(v * 100 for v in prim)
        c.magnitude = Magnitude(low=lo, mode=mid, high=hi, unit="pct_of_level")
        if c.timing.months is None and c.calculator.inputs.get("months"):
            c.timing.months = [int(x) for x in c.calculator.inputs["months"]]
        for w in r.warnings:
            driver_issues.append({"id": c.id, "level": "warn", "text": w})
    keep, more = check_cards([c for c in cards if c.magnitude is not None], years, driver_priors,
                             driver_priors.get("include_proposed", True))
    driver_issues += more
    ladder = driver_priors.get("event_status_ladder", {})

    def stub_frac(c) -> float:
        if not stub:
            return 1.0
        if c.timing.months:
            return len([x for x in c.timing.months if x > cutoff]) / len(c.timing.months)
        return rest_share

    def prof_of(c) -> np.ndarray:
        p = profile(c, years).astype(float)
        if stub and p[0] > 0:
            p[0] *= stub_frac(c)
        return p

    # Supply cards add rooms (expected in the central case, drawn per path for the range).
    supply_cards = [c for c in keep if c.affects == "supply"]
    card_rooms_central = np.zeros(len(years))
    card_rooms_by_tier: dict[str, np.ndarray] = {}
    card_rooms_paths = np.zeros((n_paths, len(years)))
    for c in supply_cards:
        p = resolve_probability(c, ladder) or 0.0
        prof = profile(c, years).astype(float)
        if stub:
            prof[0] = 0.0
        mm = c.magnitude
        mean_rooms = (mm.low + mm.mode + mm.high) / 3
        card_rooms_central += p * mean_rooms * prof
        t = tier_of(c.tier) or "average"
        card_rooms_by_tier[t] = card_rooms_by_tier.get(t, 0) + p * mean_rooms * prof
        rr = np.random.default_rng([seed, zlib.crc32(c.id.encode())])
        occ_ = rr.random(n_paths) < p
        size = rr.triangular(mm.low, mm.mode, mm.high, n_paths) if mm.high > mm.low else np.full(n_paths, mm.mode)
        S += (occ_ * size)[:, None] * prof[None, :] * 365.0
        card_rooms_paths += (occ_ * size)[:, None] * prof[None, :]

    # ---------------- Layer 2: supply volume ----------------
    # Calibrated on inventory (rooms in the market, temporarily closed hotels included), so the volume line
    # uses inventory too: new builds, unannounced supply, supply cards and removals move it; reopenings of
    # temporarily closed hotels do not (they get their own line below).
    I = np.zeros((n_paths, len(years)))
    for j, y in enumerate(years):
        idx = month_year == y
        if stub and y == y0:
            act_inv = m.loc[m["y"] == y0, "inventory_rooms"]
            I[:, j] = (act_inv.sum() + inv_m[:, idx].sum(axis=1)) / 12
        else:
            I[:, j] = inv_m[:, idx].mean(axis=1)
    I += card_rooms_paths
    I_base = float(inv_y.get(base_year, np.nan))
    if not np.isfinite(I_base):
        I_base = float(I[:, 0].mean())
    g_s = np.log(S / np.concatenate([np.full((n_paths, 1), base["supply"]), S[:, :-1]], axis=1))
    g_i = np.log(I / np.concatenate([np.full((n_paths, 1), I_base), I[:, :-1]], axis=1))
    lt = math.log1p(inv_trend)
    e = g_i - lt                                           # inventory growth above its long-run trend, per path
    e_c = e.mean(axis=0)
    prev_inv = float(inv_y.get(base_year - 1, np.nan))
    e_base = math.log(I_base / prev_inv) - lt if np.isfinite(prev_inv) and prev_inv > 0 else 0.0
    # Reopenings of temporarily closed hotels: their rooms return to available supply, with part of their demand.
    reo = sp.components["reopenings"]
    avail_prev_rooms = np.concatenate([np.full((n_paths, 1), base["supply"] / 365.0), S[:, :-1] / 365.0], axis=1)
    reo_y = np.zeros((n_paths, len(years)))
    for j, y in enumerate(years):
        idx = month_year == y
        reo_y[:, j] = reo[:, idx].sum(axis=1) / 12
    reo_add = reo_y - np.concatenate([np.zeros((n_paths, 1)), reo_y[:, :-1]], axis=1)
    s_reo = float(ppri["induced_demand"]["share_of_reopened_rooms"]["mode"])
    reo_occ = -(1 - s_reo) * reo_add / avail_prev_rooms
    reo_occ_c = reo_occ.mean(axis=0)
    bo, bo1 = float(R["supply.occ_response"]), float(R["supply.occ_response_next_year"])
    ba, ba1 = float(R["supply.adr_response"]), float(R["supply.adr_response_next_year"])

    # ---------------- Layer 2: supply quality (tier and product mix) ----------------
    avg_tier = R["quality.average_tier"]
    if avg_tier in (None, "", "auto"):
        avg_tier = cfg.average_tier or "upscale"
        if not cfg.average_tier:
            gaps.append("The market's average tier is not set; Upscale is assumed for the supply-quality lines.")
    adr_ix = {t: float(R[f"quality.adr_index.{t}"]) for t in TIERS}
    occ_ix = {t: float(R[f"quality.occ_index.{t}"]) for t in TIERS}
    decay = float(R["quality.overlap_decay"])
    exit_t = avg_tier if R["quality.exit_tier"] == "average" else R["quality.exit_tier"]
    unann_t = avg_tier if R["quality.unannounced_tier"] == "average" else R["quality.unannounced_tier"]

    def ix(tier, product=None):
        t = tier if tier in TIERS else avg_tier
        a, o = adr_ix[t], occ_ix[t]
        if product:
            a *= float(R[f"quality.product.{product}.adr"])
            o *= float(R[f"quality.product.{product}.occ"])
        return a / adr_ix[avg_tier], o / occ_ix[avg_tier]

    def overlap(tier):
        t = tier if tier in TIERS else avg_tier
        return decay ** abs(TIERS.index(t) - TIERS.index(avg_tier))

    # Average rooms in each year, by component, central (mean over paths).
    def avg_rooms(arr: np.ndarray) -> np.ndarray:
        out = np.zeros(len(years))
        for j, y in enumerate(years):
            idx = month_year == y
            out[j] = float(arr[:, idx].mean()) * (idx.sum() / 12 if stub and y == y0 else 1.0)
        return out
    removed = avg_rooms(sp.components["removed"])
    unann = avg_rooms(unann_inv)
    proj = sp.projects.copy()
    pipe_src = res.pipeline.set_index(res.pipeline["project_id"].astype(str)) if res.pipeline is not None else None
    proj_rows = []
    groups: list[tuple[str, str | None, np.ndarray]] = []    # (tier, product, avg rooms by year)
    for r in proj.itertuples():
        idx = sp.open_month.get(str(r.project_id))
        if idx is None:
            continue
        rooms_y = np.zeros(len(years))
        open_by = {}
        for j, y in enumerate(years):
            sel = np.nonzero(month_year == y)[0]
            if not len(sel):
                continue
            months_open = ((idx[:, None] >= 0) & (sel[None, :] >= idx[:, None])).sum(axis=1)
            rooms_y[j] = r.rooms * float(months_open.mean()) / 12
            open_by[str(y)] = float(((idx >= 0) & (idx <= sel[-1])).mean())
        cs = pipe_src.loc[str(r.project_id), "chain_scale"] if pipe_src is not None and str(r.project_id) in pipe_src.index else None
        if isinstance(cs, pd.Series):
            cs = cs.iloc[0]
        t = tier_of(cs)
        prod = _product(r.name)
        groups.append((t or avg_tier, prod, rooms_y))
        proj_rows.append({"project_id": str(r.project_id), "name": r.name, "rooms": float(r.rooms), "stage": r.stage,
                          "project_type": r.project_type, "chain_scale": cs, "tier": TIER_LABEL.get(t, "Unknown (market average)"),
                          "product": prod, "projected_open": r.projected_open, "p_open_horizon": r.p_open_horizon,
                          "median_open": r.median_open, "open_by_year": open_by})
    for t, arr in card_rooms_by_tier.items():
        groups.append((t if t in TIERS else avg_tier, None, arr))
    groups.append((unann_t, None, unann))
    groups.append((exit_t, None, -removed))
    B = sp.base_rooms

    def composition(j: int) -> tuple[float, float]:
        if j < 0:
            return 1.0, 1.0
        w_rooms = B
        w_occ = B * 1.0
        w_adr = B * 1.0
        for t, prod, arr in groups:
            a, o = ix(t, prod)
            w_rooms += arr[j]
            w_occ += arr[j] * o
            w_adr += arr[j] * o * a
        return w_adr / w_occ, w_occ / w_rooms           # ADR index, occupancy index

    comp = [composition(j) for j in range(len(years))]
    comp_prev = [(1.0, 1.0)] + comp[:-1]

    def delivered(j: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for t, prod, arr in groups[:-1]:                 # additions only (not removals)
            d = arr[j] - (arr[j - 1] if j > 0 else 0.0)
            if d > 0:
                out[t] = out.get(t, 0.0) + d
        return out

    def overlap_factor(j: int) -> float:
        if j < 0:
            return 1.0
        d = delivered(j)
        tot = sum(d.values())
        return sum(v * overlap(t) for t, v in d.items()) / tot if tot > 0 else 1.0

    # ---------------- Layer 3: expected driver lines ----------------
    p_base = float(R["shocks.annual_probability"])
    sh_occ, sh_adr = float(R["shocks.occ_impact"]), float(R["shocks.adr_impact"])
    rec = float(R["shocks.recovery_next_year"])
    drv_lines: dict[int, list[dict]] = {j: [] for j in range(len(years))}
    drv_dev_occ = np.zeros((n_paths, len(years)))
    drv_dev_adr = np.zeros((n_paths, len(years)))
    driver_rows = []
    for c in keep:
        if c.affects == "supply":
            continue
        p = resolve_probability(c, ladder) or 0.0
        prof = prof_of(c)
        rr = np.random.default_rng([seed, zlib.crc32(c.id.encode()), 7])
        occurs = rr.random(n_paths) < p
        ev = {"title": c.title, "p": p}
        if c.affects == "shock_probability":
            mm = c.magnitude
            mult = (mm.low + mm.mode + mm.high) / 3
            q = np.clip((mult - 1) * p_base * prof, 0, 1)        # extra annual chance of a downturn, if it applies
            lvl_occ, lvl_adr = p * q * sh_occ, p * q * sh_adr
            g_occ = lvl_occ - np.concatenate([[0.0], lvl_occ[:-1]]) * rec
            g_adr = lvl_adr - np.concatenate([[0.0], lvl_adr[:-1]]) * rec
            hit = (rr.random((n_paths, len(years))) < q[None, :]) & occurs[:, None]
            po, pa = hit * sh_occ, hit * sh_adr
            po = po - np.concatenate([np.zeros((n_paths, 1)), po[:, :-1]], axis=1) * rec
            pa = pa - np.concatenate([np.zeros((n_paths, 1)), pa[:, :-1]], axis=1) * rec
            drv_dev_occ += po - g_occ[None, :]
            drv_dev_adr += pa - g_adr[None, :]
            text_size = (f"raises the market's {p_base:.1%} annual chance of a downturn by x{mult:.2f}; a downturn year moves "
                         f"occupancy {_pct(sh_occ)} and ADR {_pct(sh_adr)} (European panel)")
        else:
            co = calc_out.get(c.id)
            mm = c.magnitude
            if co is not None:
                dem_m, adr_m = co.demand_mean, co.adr_mean
                dem_t, adr_t = co.demand, co.adr
            else:
                mean = (mm.low + mm.mode + mm.high) / 300
                tri = (mm.low / 100, mm.mode / 100, mm.high / 100)
                dem_m, adr_m = (mean, 0.0) if c.affects == "demand" else (0.0, mean)
                dem_t, adr_t = (tri, (0, 0, 0)) if c.affects == "demand" else ((0, 0, 0), tri)
            lvl_occ, lvl_adr = p * dem_m * prof, p * adr_m * prof
            g_occ = lvl_occ - np.concatenate([[0.0], lvl_occ[:-1]])
            g_adr = lvl_adr - np.concatenate([[0.0], lvl_adr[:-1]])

            def draw(t3):
                lo, mo, hi = sorted(t3)
                if mo < lo or mo > hi:
                    mo = (lo + hi) / 2
                return rr.triangular(lo, mo, hi, n_paths) if hi > lo else np.full(n_paths, mo)
            so, sa = draw(dem_t) * occurs, draw(adr_t) * occurs
            po, pa = so[:, None] * prof[None, :], sa[:, None] * prof[None, :]
            po = po - np.concatenate([np.zeros((n_paths, 1)), po[:, :-1]], axis=1)
            pa = pa - np.concatenate([np.zeros((n_paths, 1)), pa[:, :-1]], axis=1)
            drv_dev_occ += po - g_occ[None, :]
            drv_dev_adr += pa - g_adr[None, :]
            parts = []
            if abs(dem_m) > 1e-6:
                parts.append(f"demand {_pct(dem_m, 2)}")
            if abs(adr_m) > 1e-6:
                parts.append(f"ADR {_pct(adr_m, 2)}")
            text_size = "if it happens: " + " and ".join(parts) + " of the year's level"
        basis = f"driver ({c.status})"
        for j, y in enumerate(years):
            if abs(g_occ[j]) < 5e-6 and abs(g_adr[j]) < 5e-6:
                continue
            payback = j > 0 and prof[j] < prof[j - 1]
            txt = (f"{p:.0%} chance" + (f" ({c.event_status.replace('_', ' ')})" if c.event_status else "")
                   + f"; {text_size}." + (" The effect unwinds this year (the level returns to normal)." if payback else ""))
            drv_lines[j].append(_line(f"driver:{c.id}", "Drivers", c.title, g_occ[j], g_adr[j], txt, basis,
                                      "; ".join(calc_out[c.id].working) if c.id in calc_out else (c.magnitude_note or "")))
        driver_rows.append({"id": c.id, "title": c.title, "affects": c.affects, "category": c.category, "probability": p,
                            "status": c.status, "event_status": c.event_status, "calculator": c.calculator.type if c.calculator else None,
                            "working": calc_out[c.id].working if c.id in calc_out else [],
                            "magnitude": c.magnitude.model_dump() if c.magnitude else None,
                            "evidence": [e_.model_dump() for e_ in c.evidence], "profile": {str(y): float(v) for y, v in zip(years, prof)}})

    # ---------------- Assemble the ledger, year by year ----------------
    pass_through = float(R["baseline.inflation_pass_through"])
    cpi_mean = anchor.cpi_mean
    cpi_src = {int(v["year"]): v for v in (macro.get("cpi_near_term") or {}).get("values", [])}
    if not cpi:
        gaps.append("No inflation history loaded: the inflation line is omitted (rate follows the long-run CAGR only).")
    ceiling = float(hist.ceiling["annual"])
    out_years = []
    occ_lvl, adr_lvl = base["occ"], base["adr"]
    central = {"occ": np.zeros(len(years)), "adr": np.zeros(len(years))}
    sup_dev_occ = np.zeros((n_paths, len(years)))
    sup_dev_adr = np.zeros((n_paths, len(years)))
    stub_info = None
    for j, y in enumerate(years):
        lines = []
        if stub and y == y0:
            cur = m[m["period_start"].dt.year == y0]
            prev = m[m["period_start"].dt.year == y0 - 1]
            pytd, prest = prev[prev["period_start"].dt.month <= cutoff], prev[prev["period_start"].dt.month > cutoff]
            o_ytd = (cur["demand"].sum() / cur["supply"].sum()) / (pytd["demand"].sum() / pytd["supply"].sum())
            a_ytd = (cur["revenue"].sum() / cur["demand"].sum()) / (pytd["revenue"].sum() / pytd["demand"].sum())
            sup_rest = float(S[:, j].mean()) - float(cur["supply"].sum())
            ratio = sup_rest / float(prest["supply"].sum()) if prest["supply"].sum() > 0 else 1.0
            dem_rest = prest["demand"].to_numpy() * ratio * o_ytd
            rev_rest = dem_rest * (prest["revenue"] / prest["demand"]).to_numpy() * a_ytd
            occ_s = (cur["demand"].sum() + dem_rest.sum()) / (cur["supply"].sum() + sup_rest)
            adr_s = (cur["revenue"].sum() + rev_rest.sum()) / (cur["demand"].sum() + dem_rest.sum())
            stub_info = {"months_actual": cutoff, "occ_ytd": o_ytd - 1, "adr_ytd": a_ytd - 1,
                         "revpar_ytd": o_ytd * a_ytd - 1}
            rest_txt = (f"{MONTHS[cutoff]} to Dec follow last year's same months moved by the year-to-date trend"
                        if cutoff < 12 else "")
            lines.append(_line("stub", "Current year", f"Jan to {MONTHS[cutoff - 1]} actual, rest at the year-to-date trend",
                               occ_s / base["occ"] - 1, adr_s / base["adr"] - 1,
                               f"{cutoff} months are actual: occupancy {_pct(o_ytd - 1)} and ADR {_pct(a_ytd - 1)} against the same "
                               f"months of {y0 - 1}. {rest_txt}.", "fact"))
        else:
            lines.append(_line("baseline", "Baseline", f"Long-run RevPAR CAGR {anchor.start} to {anchor.end}", base_occ_g, base_adr_g,
                               f"RevPAR grew {_pct(anchor.revpar)} a year from {anchor.start} to {anchor.end} (occupancy {_pct(anchor.occupancy)}, "
                               f"ADR {_pct(anchor.adr)}). "
                               + ("Occupancy is held flat and rate carries the RevPAR growth." if split == "flat"
                                  else "Occupancy and ADR each grow at their own long-run rate."),
                               "user_decision" if split == "flat" else "fact"))
            ci = cpi_rate(macro, y) if cpi else None
            if ci is not None and cpi_mean is not None:
                v = pass_through * (ci - cpi_mean)
                src = cpi_src.get(y)
                srct = f"{src['publisher']}, {src['published'][:7]}" if src else "IMF World Economic Outlook"
                lines.append(_line("inflation", "Baseline", "Inflation against its historical average", 0.0, v,
                                   f"Inflation {ci:.1%} ({srct}) against {cpi_mean:.1%} a year over {anchor.start} to {anchor.end}; "
                                   f"rate moves {pass_through:g} point per point.", "assumption" if pass_through != 1 else "fact",
                                   R.reg["params"]["baseline.inflation_pass_through"]["evidence"]))
            # supply volume
            e_prev = e_c[j - 1] if j > 0 else e_base
            ov, ov_prev = overlap_factor(j), overlap_factor(j - 1) if j > 0 else 1.0
            vo, va = bo * e_c[j] + bo1 * e_prev, ba * e_c[j] + ba1 * e_prev
            g_tot = float(g_i[:, j].mean())
            txt = (f"Hotel inventory grows {_pct(math.expm1(g_tot))} against its long-run trend of {_pct(inv_trend)}: "
                   f"{_pts(e_c[j])} against trend this year and {_pts(e_prev)} last year. Across European submarkets each point of "
                   f"inventory growth above trend has moved occupancy {bo:+.2f} points the same year and ADR {ba1:+.2f} points the next.")
            lines.append(_line("supply_volume", "Supply", "Supply against its long-run trend", vo, va, txt, "fitted",
                               R.reg["params"]["supply.occ_response"]["evidence"]))
            if abs(reo_occ_c[j]) > 1e-5:
                lines.append(_line("reopenings", "Supply", "Temporarily closed hotels reopening", reo_occ_c[j], 0.0,
                                   f"Closed hotels return {float(reo_add[:, j].mean()):,.0f} rooms on average this year (expected value). "
                                   f"Their rooms come back in full but only {s_reo:.0%} of their former demand is assumed to return with them.",
                                   "assumption", "Share of reopened rooms' demand that returns: config/pipeline_priors.yaml."))
            # supply quality: mix and overlap
            (a1, o1), (a0, o0) = comp[j], comp_prev[j]
            mix_a, mix_o = a1 / a0 - 1, o1 / o0 - 1
            d = delivered(j)
            if d or abs(mix_a) > 1e-5:
                tot = sum(d.values())
                mixdesc = ", ".join(f"{TIER_LABEL.get(t, t)} {v / tot:.0%}" for t, v in sorted(d.items(), key=lambda kv: -kv[1])) if tot else "none"
                lines.append(_line("supply_mix", "Supply quality", "Tier and product mix of hotels opening and closing", mix_o, mix_a,
                                   f"About {tot:,.0f} rooms on average arrive this year (expected value), by tier: {mixdesc}; "
                                   f"against a market priced like {TIER_LABEL[avg_tier]}. Closures are assumed {TIER_LABEL.get(exit_t, exit_t)}. "
                                   "Blending their rates and occupancy into the market average moves the averages by this much, "
                                   "before any competition effect.", "assumption",
                                   "Tier indices and the market's average tier are assumptions until class-level CoStar data is loaded."))
            oo = (ov - 1) * bo * e_c[j] + (ov_prev - 1) * bo1 * e_prev
            oa = (ov - 1) * ba * e_c[j] + (ov_prev - 1) * ba1 * e_prev
            if abs(oo) > 1e-5 or abs(oa) > 1e-5:
                lines.append(_line("supply_overlap", "Supply quality", "How directly the new rooms compete", oo, oa,
                                   f"The new rooms compete {ov:.0%} as directly as rooms at the market's average tier "
                                   f"({overlap(avg_tier):.0%} for the same tier, x{decay:g} per tier step), so the supply pressure above is "
                                   f"scaled by that share.", "assumption", R.reg["params"]["quality.overlap_decay"]["evidence"]))
            # per-path supply deviations for the range
            e_prev_p = e[:, j - 1] if j > 0 else np.full(n_paths, e_base)
            sup_dev_occ[:, j] = ov * bo * (e[:, j] - e_c[j]) + ov_prev * bo1 * (e_prev_p - (e_c[j - 1] if j > 0 else e_base))
            sup_dev_adr[:, j] = ov * ba * (e[:, j] - e_c[j]) + ov_prev * ba1 * (e_prev_p - (e_c[j - 1] if j > 0 else e_base))
            sup_dev_occ[:, j] += reo_occ[:, j] - reo_occ_c[j]
        lines += drv_lines[j]
        occ_g = sum(l_["occ"] for l_ in lines)
        nxt = occ_lvl * (1 + occ_g)
        if nxt > ceiling:
            cut = ceiling / occ_lvl - 1 - occ_g
            lines.append(_line("ceiling", "Limits", "Occupancy ceiling", cut, 0.0,
                               f"Occupancy would reach {nxt:.1%}, above the ceiling of {ceiling:.1%} ({hist.ceiling['basis']}).", "fact"))
            occ_g += cut
        adr_g = sum(l_["adr"] for l_ in lines)
        occ_lvl, adr_lvl = occ_lvl * (1 + occ_g), adr_lvl * (1 + adr_g)
        central["occ"][j], central["adr"][j] = occ_g, adr_g
        out_years.append({"year": y, "label": f"{y} (Jan to {MONTHS[cutoff - 1]} actual)" if stub and y == y0 else str(y),
                          "is_stub": bool(stub and y == y0), "lines": lines, "occ_growth": occ_g, "adr_growth": adr_g,
                          "revpar_growth": (1 + occ_g) * (1 + adr_g) - 1, "occupancy": occ_lvl, "adr": adr_lvl,
                          "revpar": occ_lvl * adr_lvl})

    # ---------------- Ranges ----------------
    lib = R.reg.get("ranges") or {}
    spread = float(R["ranges.spread_multiplier"])
    vol = {"occ": 1.0, "adr": 1.0}
    vol_note = {}
    sfx = "_flat" if split == "flat" else ""
    h1 = (lib.get("horizons") or {}).get(1) or (lib.get("horizons") or {}).get("1")
    if h1:
        u = ha[~ha["year"].isin(covid)].set_index("year").sort_index()
        qs_ = lib["quantile_levels"]
        for k, col, flag in (("occ", "occupancy", "use_occ"), ("adr", "adr", "use_adr")):
            g = np.log(u[col]).diff()
            if flag in u:
                g = g[u[flag].astype(bool)]
            g = g.dropna()
            panel_spread = h1[k + sfx][qs_.index(90)] - h1[k + sfx][qs_.index(10)]
            if len(g) >= 6 and panel_spread > 0:
                own = float(np.percentile(g, 90) - np.percentile(g, 10))
                n_ = len(g)
                r_ = (n_ * own / panel_spread + 8 * 1.0) / (n_ + 8)        # shrunk toward the European norm
                vol[k] = float(np.clip(r_, 0.4, 2.5))
                vol_note[k] = {"market_p10_p90_spread": own, "panel_spread": panel_spread, "years": n_}
    rng2 = np.random.default_rng([seed, 99])
    rho = float(lib.get("corr_occ_adr_flat" if split == "flat" else "corr_occ_adr", 0.2))
    phi = float(lib.get("autocorr", 0.1))
    n_y = len(years)
    z1 = np.zeros((n_paths, n_y))
    z2 = np.zeros((n_paths, n_y))
    for j in range(n_y):
        a_ = rng2.standard_normal(n_paths)
        b_ = rho * a_ + math.sqrt(1 - rho * rho) * rng2.standard_normal(n_paths)
        z1[:, j] = (phi * z1[:, j - 1] + math.sqrt(1 - phi * phi) * a_) if j else a_
        z2[:, j] = (phi * z2[:, j - 1] + math.sqrt(1 - phi * phi) * b_) if j else b_
    err_o = np.zeros((n_paths, n_y))
    err_a = np.zeros((n_paths, n_y))
    for j, y in enumerate(years):
        if stub and y == y0:
            err_o[:, j] = z1[:, j] * float(R["ranges.stub_error_occ"]) * rest_share
            err_a[:, j] = z2[:, j] * float(R["ranges.stub_error_adr"]) * rest_share
            continue
        h = min(j + (0 if stub else 1), 5)
        hz = (lib.get("horizons") or {}).get(h) or (lib.get("horizons") or {}).get(str(h))
        if hz:
            err_o[:, j] = _q_from_z(z1[:, j], lib["quantile_levels"], hz["occ" + sfx]) * vol["occ"]
            err_a[:, j] = _q_from_z(z2[:, j], lib["quantile_levels"], hz["adr" + sfx]) * vol["adr"]
        else:
            err_o[:, j] = z1[:, j] * 0.05
            err_a[:, j] = z2[:, j] * 0.07
            gaps.append("No calibrated error library: ranges use placeholder widths.") if j == 1 else None
    go = central["occ"][None, :] + spread * err_o + sup_dev_occ + drv_dev_occ
    ga = central["adr"][None, :] + spread * err_a + sup_dev_adr + drv_dev_adr
    occ_p = base["occ"] * np.cumprod(1 + go, axis=1)
    occ_p = np.clip(occ_p, 0.05, ceiling)
    adr_p = base["adr"] * np.cumprod(1 + ga, axis=1)
    rev_p = occ_p * adr_p
    prev = lambda arr, b0: np.concatenate([np.full((n_paths, 1), b0), arr[:, :-1]], axis=1)  # noqa: E731
    g_occ_p = occ_p / prev(occ_p, base["occ"]) - 1
    g_adr_p = adr_p / prev(adr_p, base["adr"]) - 1
    g_rev_p = rev_p / prev(rev_p, base["occ"] * base["adr"]) - 1

    def qd(arr, j):
        return {"p10": float(np.percentile(arr[:, j], 10)), "p50": float(np.percentile(arr[:, j], 50)),
                "p90": float(np.percentile(arr[:, j], 90))}
    for j, yr in enumerate(out_years):
        yr["range"] = {"occ_growth": qd(g_occ_p, j), "adr_growth": qd(g_adr_p, j), "revpar_growth": qd(g_rev_p, j),
                       "occupancy": qd(occ_p, j), "adr": qd(adr_p, j), "revpar": qd(rev_p, j),
                       "p_revpar_fall": float((g_rev_p[:, j] < 0).mean())}
    # five-year RevPAR CAGR over the full years
    jf = [j for j, y in enumerate(years) if y in full_years]
    rev0 = out_years[jf[0] - 1]["revpar"] if jf[0] > 0 else base["occ"] * base["adr"]
    n5 = len(jf)
    c5 = (out_years[jf[-1]]["revpar"] / rev0) ** (1 / n5) - 1
    rev0_p = rev_p[:, jf[0] - 1] if jf[0] > 0 else base["occ"] * base["adr"]
    c5p = (rev_p[:, jf[-1]] / rev0_p) ** (1 / n5) - 1
    five = {"from": years[jf[0]] - 1, "to": years[jf[-1]], "revpar_cagr": c5, "p10": float(np.percentile(c5p, 10)),
            "p90": float(np.percentile(c5p, 90)), "baseline_revpar_cagr": anchor.revpar,
            "difference": c5 - anchor.revpar}

    # ---------------- Supply summary ----------------
    sup_years = []
    for j, y in enumerate(years):
        sup_years.append({"year": y, "supply_growth": float(math.expm1(g_s[:, j].mean())),
                          "inventory_growth": float(math.expm1(g_i[:, j].mean())), "excess_vs_trend": float(e_c[j]),
                          "new_builds_rooms": float(avg_rooms(sp.components["new_builds"])[j]),
                          "reopening_rooms": float(avg_rooms(sp.components["reopenings"])[j]),
                          "unannounced_rooms": float(unann[j]), "removed_rooms": float(removed[j]),
                          "adr_mix_index": comp[j][0], "occ_mix_index": comp[j][1], "overlap": overlap_factor(j)})

    provisional = any(c.status == "proposed" for c in keep)
    if not keep:
        gaps.append("No drivers loaded: the forecast is the baseline and supply only (layers 1 and 2).")
    if res.pipeline is None or res.pipeline.empty:
        gaps.append("No pipeline loaded: supply follows the long-run trend.")
    hist_rows = [{"year": int(y), "occupancy": float(r_["occupancy"]), "adr": float(r_["adr"]), "revpar": float(r_["revpar"]),
                  "supply": float(r_["supply"]), "demand": float(r_["demand"]), "complete": bool(r_["complete"])}
                 for y, r_ in act.iterrows()]
    return {
        "meta": {"engine": "ledger", "engine_version": __version__, "seed": seed, "paths": n_paths, "stub_year": y0 if stub else None,
                 "base_year": base_year, "last_actual_month": last.strftime("%Y-%m"), "currency": cfg.currency,
                 "registry_version": R.version(), "calibrated_on": R.reg.get("calibrated_on"),
                 "panel_snapshot": R.reg.get("panel_snapshot"), "provisional": provisional,
                 "seconds": round(time.time() - t0, 2)},
        "baseline": {"start": anchor.start, "end": anchor.end, "revpar": anchor.revpar, "occupancy": anchor.occupancy,
                     "adr": anchor.adr, "supply": anchor.supply, "demand": anchor.demand, "cpi_mean": anchor.cpi_mean,
                     "split": split, "occ_growth": base_occ_g, "adr_growth": base_adr_g},
        "years": out_years,
        "five_year": five,
        "stub": stub_info,
        "supply": {"trend": inv_trend, "inventory_trend": inv_trend, "base_rooms": sp.base_rooms, "average_tier": TIER_LABEL[avg_tier], "years": sup_years,
                   "projects": proj_rows, "notes": sp.notes},
        "drivers": {"applied": driver_rows, "issues": driver_issues, "ladder": ladder,
                    "cards": [c.model_dump(exclude_none=True) for c in cards]},
        "ranges": {"source": lib.get("source"), "volatility_ratio": vol, "volatility_basis": vol_note, "spread_multiplier": spread,
                   "note": "Ranges combine the historical miss of the CAGR rule (centred, scaled to this market's volatility) "
                           "with pipeline and driver uncertainty. The central forecast is the ledger total."},
        "calibration": {"version": R.version(), "overridden": sorted(R.overridden),
                        "evidence": R.reg.get("evidence", {})},
        "adjustments": adjustments,
        "gaps": gaps,
        "history": hist_rows,
        "macro": {"cpi_path": {str(y): cpi_rate(macro, y) for y in years} if cpi else None,
                  "cpi_near_term": macro.get("cpi_near_term")},
        "ceiling": hist.ceiling,
    }
