"""Calibrate the ledger's coefficients from the private European panel.

python -m engine.ledger.calibrate [--data-dir /home/user/marketsdata] [--dry-run]

Fits, on every submarket in the panel (monthly CoStar actuals, licensed, private repo):
- supply response: two-way fixed-effects regressions of occupancy, ADR and RevPAR log growth on
  inventory growth this year and last (submarket and year effects, so the coefficient is the
  effect of supply growth above a submarket's own normal, net of Europe-wide cycles);
- the error library of the long-run CAGR rule (rolling origins, horizons 1 to 5), which sets the
  ranges, plus a backtest of the rule's five-year accuracy;
- downturn frequency and depth;
- past event effects (config/event_analogs.yaml), monthly difference-in-differences.

Only pooled coefficients and pooled statistics are written to config/calibration.yaml (public).
Per-submarket and per-event results go to <data-dir>/derived/ in the private repository.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from engine.config import CONFIG_DIR

COVID = {2020, 2021, 2022}
QS = [5, 10, 25, 50, 75, 90, 95]


def annual_panel(monthly: pd.DataFrame) -> pd.DataFrame:
    m = monthly[monthly["is_actual"] & (monthly["slice"] == "All") & monthly["supply"].notna()].copy()
    m["year"] = m["period"].dt.year
    g = m.groupby(["slug", "market", "year"])
    a = g[["supply", "demand", "revenue"]].sum(min_count=1)
    a["months"] = g["supply"].count()
    a["inv"] = g["inventory_rooms"].mean()
    a = a[a["months"] == 12].reset_index()
    a["occ"] = a["demand"] / a["supply"]
    a["adr"] = a["revenue"] / a["demand"]
    a["revpar"] = a["revenue"] / a["supply"]
    a = a.sort_values(["slug", "year"]).reset_index(drop=True)
    for c in ("occ", "adr", "revpar", "inv", "demand", "supply"):
        a["g_" + c] = a.groupby("slug")[c].transform(lambda s: np.log(s).diff())
    # a gap year breaks the growth chain
    gap = a.groupby("slug")["year"].diff() != 1
    a.loc[gap, [c for c in a.columns if c.startswith("g_")]] = np.nan
    return a


def _twfe(df: pd.DataFrame, y: str, xs: list[str], iters: int = 40) -> tuple[np.ndarray, np.ndarray]:
    X, Y = df[xs].astype(float).copy(), df[y].astype(float).copy()
    for _ in range(iters):
        for k in ("slug", "year"):
            Y = Y - Y.groupby(df[k]).transform("mean")
            X = X - X.groupby(df[k]).transform("mean")
    Xv, Yv = X.to_numpy(), Y.to_numpy()
    b, *_ = np.linalg.lstsq(Xv, Yv, rcond=None)
    e = Yv - Xv @ b
    inv = np.linalg.inv(Xv.T @ Xv)
    meat = np.zeros((len(xs), len(xs)))
    for idx in df.groupby("slug").indices.values():
        u = Xv[idx].T @ e[idx]
        meat += np.outer(u, u)
    return b, np.sqrt(np.diag(inv @ meat @ inv))


def supply_response(a: pd.DataFrame) -> dict:
    d = a.copy()
    d["g_inv_l1"] = d.groupby("slug")["g_inv"].shift()
    d = d[~d["year"].isin(COVID | {2023})].dropna(subset=["g_occ", "g_adr", "g_revpar", "g_inv", "g_inv_l1", "g_demand"])
    for c in ("g_occ", "g_adr", "g_revpar", "g_inv", "g_inv_l1", "g_demand"):
        lo, hi = d[c].quantile([0.01, 0.99])
        d[c] = d[c].clip(lo, hi)
    out = {"n": int(len(d)), "submarkets": int(d["slug"].nunique()), "years": [int(d["year"].min()), int(d["year"].max())]}
    for y in ("g_occ", "g_adr", "g_revpar", "g_demand"):
        b, se = _twfe(d, y, ["g_inv", "g_inv_l1"])
        out[y] = {"same_year": float(b[0]), "same_year_se": float(se[0]), "next_year": float(b[1]), "next_year_se": float(se[1])}
    return out


def error_library(a: pd.DataFrame) -> tuple[dict, dict]:
    rows, bt = [], []
    for slug, s in a.groupby("slug"):
        s = s.set_index("year")
        yrs = list(s.index)
        lg = np.log(s[["occ", "adr", "revpar", "inv"]])
        gr = s[["g_occ", "g_adr", "g_revpar"]]
        first = yrs[0]
        for T in yrs:
            if T in COVID or T - first < 5:
                continue
            cagr = (lg.loc[T] - lg.loc[first]) / (T - first)
            for h in range(1, 6):
                y = T + h
                if y not in gr.index or y in COVID or not np.isfinite(gr.loc[y, "g_occ"]):
                    continue
                rows.append({"h": h, "occ": gr.loc[y, "g_occ"] - cagr["occ"], "adr": gr.loc[y, "g_adr"] - cagr["adr"],
                             "occ_flat": gr.loc[y, "g_occ"], "adr_flat": gr.loc[y, "g_adr"] - cagr["revpar"],
                             "key": f"{slug}|{T}"})
            if all((T + h) in lg.index and (T + h) not in COVID for h in range(1, 6)):
                bt.append({"actual": (lg.loc[T + 5, "revpar"] - lg.loc[T, "revpar"]) / 5, "cagr": cagr["revpar"],
                           "excess": ((lg.loc[T + 5, "inv"] - lg.loc[T, "inv"]) - 5 * cagr["inv"]) / 5})
    e = pd.DataFrame(rows)
    lib = {"n": int(len(e)), "origins": int(e["key"].nunique()), "quantile_levels": QS, "horizons": {}}
    for h, g in e.groupby("h"):
        lib["horizons"][int(h)] = {k: [float(v) for v in np.percentile(g[k], QS)] for k in ("occ", "adr", "occ_flat", "adr_flat")}
    lib["corr_occ_adr"] = float(np.corrcoef(e["occ"], e["adr"])[0, 1])
    lib["corr_occ_adr_flat"] = float(np.corrcoef(e["occ_flat"], e["adr_flat"])[0, 1])
    w = e.pivot_table(index="key", columns="h", values="adr")
    lib["autocorr"] = float(np.nanmean([w[h].corr(w[h + 1]) for h in range(1, 5)]))
    x = a[~a["year"].isin(COVID)]
    sd = x.groupby("slug")[["g_occ", "g_adr"]].agg(lambda v: (v.quantile(0.75) - v.quantile(0.25)) / 1.349)
    lib["panel_median_sd"] = {"occ": float(sd["g_occ"].median()), "adr": float(sd["g_adr"].median())}
    b = pd.DataFrame(bt)
    k_supply = None
    backtest = {"n": int(len(b)), "mae_cagr": float(np.mean(np.abs(b["actual"] - b["cagr"]))),
                "bias_cagr": float(np.mean(b["actual"] - b["cagr"])), "mae_zero": float(np.mean(np.abs(b["actual"])))}
    return lib, {**backtest, "_frame": b, "_k": k_supply}


def shock_stats(a: pd.DataFrame) -> dict:
    x = a[~a["year"].isin(COVID | {2023})].dropna(subset=["g_revpar"]).copy()
    for c in ("g_occ", "g_adr", "g_revpar"):
        x["d_" + c] = x[c] - x.groupby("slug")[c].transform("median")
    sh = x[x["d_g_revpar"] < -0.10]
    nxt = x.set_index(["slug", "year"])
    rec = [nxt.loc[(s, y + 1), "d_g_revpar"] for s, y in zip(sh["slug"], sh["year"]) if (s, y + 1) in nxt.index]
    return {"n_years": int(len(x)), "n_shocks": int(len(sh)), "annual_probability": float(len(sh) / len(x)),
            "occ_impact": float(sh["d_g_occ"].mean()), "adr_impact": float(sh["d_g_adr"].mean()),
            "revpar_impact": float(sh["d_g_revpar"].mean()),
            "recovery_next_year": float(np.mean(rec) / -sh["d_g_revpar"].mean()) if rec else 0.0,
            "top_years": {int(k): int(v) for k, v in sh["year"].value_counts().head(6).items()}}


def event_analogs(monthly: pd.DataFrame, events: list[dict]) -> tuple[list[dict], dict]:
    m = monthly[monthly["is_actual"] & (monthly["slice"] == "All") & monthly["supply"].notna()].copy()
    m["year"], m["month"] = m["period"].dt.year, m["period"].dt.month
    m = m.sort_values(["slug", "period"])
    for c in ("occupancy", "adr", "demand"):
        m["yoy_" + c] = m.groupby(["slug", "month"])[c].transform(lambda s: np.log(s).diff())
    per_event = []
    for ev in events:
        months, yr = ev["months"], ev["year"]
        res = []
        if len(months) == 12:
            def jan_sep(mk):
                s = m[m["market"].isin(mk) & m["year"].isin([yr - 1, yr]) & (m["month"] <= 9)]
                t = s.groupby(["slug", "year"])[["supply", "demand", "revenue"]].sum().unstack("year")
                occ = np.log((t["demand"][yr] / t["supply"][yr]) / (t["demand"][yr - 1] / t["supply"][yr - 1]))
                adr = np.log((t["revenue"][yr] / t["demand"][yr]) / (t["revenue"][yr - 1] / t["demand"][yr - 1]))
                return occ, adr
            o1, a1 = jan_sep(ev["markets"])
            o0, a0 = jan_sep(ev.get("comparison_markets", []))
            res = [{"slug": s, "occ": float(o1[s] - o0.median()), "adr": float(a1[s] - a0.median())} for s in o1.index]
        else:
            s = m[m["market"].isin(ev["markets"]) & (m["year"] == yr)]
            adj = set(months) | {min(months) - 1, max(months) + 1}
            for slug, g in s.groupby("slug"):
                evm, oth = g[g["month"].isin(months)], g[~g["month"].isin(adj)]
                if len(evm) < len(months) or len(oth) < 3:
                    continue
                base_o, base_a = oth["yoy_occupancy"].median(), oth["yoy_adr"].median()
                res.append({"slug": slug, "occ": float(evm["yoy_occupancy"].mean() - base_o),
                            "adr": float(evm["yoy_adr"].mean() - base_a),
                            "adr_month_before": float(g[g["month"] == min(months) - 1]["yoy_adr"].mean() - base_a),
                            "adr_month_after": float(g[g["month"] == max(months) + 1]["yoy_adr"].mean() - base_a),
                            "event_demand_share": float(evm["demand"].sum() / g["demand"].sum())})
        r = pd.DataFrame(res)
        if r.empty:
            continue
        per_event.append({"id": ev["id"], "name": ev["name"], "type": ev["type"], "n_submarkets": int(len(r)),
                          "median": {k: float(r[k].median()) for k in r.columns if k != "slug"},
                          "p10": {k: float(r[k].quantile(0.1)) for k in r.columns if k != "slug"},
                          "p90": {k: float(r[k].quantile(0.9)) for k in r.columns if k != "slug"},
                          "by_submarket": res})
    pooled = {}
    for t in sorted({e["type"] for e in per_event}):
        es = [e for e in per_event if e["type"] == t]
        keys = set.intersection(*(set(e["median"]) for e in es))
        pooled[t] = {"n_events": len(es), "n_submarkets": int(sum(e["n_submarkets"] for e in es)),
                     **{k: float(np.median([e["median"][k] for e in es])) for k in sorted(keys)}}
    return per_event, pooled


def _set(reg: dict, key: str, value, evidence: str):
    p = reg["params"][key]
    p["value"] = round(float(value), 4) if isinstance(value, (int, float, np.floating)) else value
    p["evidence"] = evidence
    p["fitted_on"] = date.today().isoformat()


def apply_to_registry(reg: dict, fit: dict, snapshot: str) -> dict:
    sr, lib, sh, ev = fit["supply"], fit["errors"], fit["shocks"], fit["events"]
    base = f"European panel, CoStar snapshot {snapshot}"
    fe = f"{base}: {sr['submarkets']} submarkets, {sr['n']} submarket-years ({sr['years'][0]} to {sr['years'][1]}, COVID years excluded); two-way fixed effects, standard errors clustered by submarket"
    o, d, r, dm = sr["g_occ"], sr["g_adr"], sr["g_revpar"], sr["g_demand"]
    _set(reg, "supply.occ_response", o["same_year"], f"{fe}. Estimate {o['same_year']:+.3f} (se {o['same_year_se']:.3f}).")
    _set(reg, "supply.occ_response_next_year", o["next_year"], f"{fe}. Estimate {o['next_year']:+.3f} (se {o['next_year_se']:.3f}).")
    _set(reg, "supply.adr_response", d["same_year"], f"{fe}. Estimate {d['same_year']:+.3f} (se {d['same_year_se']:.3f}).")
    _set(reg, "supply.adr_response_next_year", d["next_year"], f"{fe}. Estimate {d['next_year']:+.3f} (se {d['next_year_se']:.3f}).")
    reg["evidence"]["supply_response"] = {"revpar_same_year": r["same_year"], "revpar_next_year": r["next_year"],
                                          "demand_same_year": dm["same_year"], "source": fe}
    _set(reg, "shocks.annual_probability", sh["annual_probability"],
         f"{base}: {sh['n_shocks']} of {sh['n_years']} submarket-years outside COVID had RevPAR growth more than 10 points below the submarket's median (mostly {', '.join(map(str, list(sh['top_years'])[:4]))}).")
    _set(reg, "shocks.adr_impact", sh["adr_impact"], f"{base}: average ADR growth in those years relative to the submarket's median.")
    _set(reg, "shocks.occ_impact", sh["occ_impact"], f"{base}: average occupancy growth in those years relative to the submarket's median.")
    _set(reg, "shocks.recovery_next_year", max(0.0, min(1.0, sh["recovery_next_year"])),
         f"{base}: share of the RevPAR fall regained, on average, in the following year.")
    reg["ranges"] = {"source": f"{base}: errors of the long-run CAGR rule at {lib['origins']} rolling origins ({lib['n']} forecast years, COVID years excluded).",
                     **{k: v for k, v in lib.items() if k not in ("n", "origins")}}
    for t, v in ev.items():
        for k, key in (("adr", "adr"), ("occ", "occ"), ("adr_month_before", "adr_month_before"), ("adr_month_after", "adr_month_after")):
            pk = f"events.{t}.{key}"
            if pk in reg["params"] and k in v:
                _set(reg, pk, v[k], f"{base}: median of {v['n_events']} event(s), {v['n_submarkets']} host submarkets; event months against the same year's other months.")
    bt = fit["backtest"]
    reg["evidence"]["cagr_rule_backtest"] = {
        "five_year_revpar_cagr_mae": bt["mae_cagr"], "bias": bt["bias_cagr"], "mae_zero_growth": bt["mae_zero"],
        "mae_with_supply_adjustment": bt.get("mae_supply_adj"), "n": bt["n"],
        "source": f"{base}: whole-history CAGR at each origin against the next five years' RevPAR CAGR."}
    reg["calibrated_on"] = date.today().isoformat()
    reg["panel_snapshot"] = snapshot
    return reg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=Path("/home/user/marketsdata"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    monthly = pd.read_parquet(a.data_dir / "panel" / "performance_monthly.parquet")
    snapshot = json.loads((a.data_dir / "manifest.json").read_text())["panel"]["latest_snapshot"]
    ann = annual_panel(monthly)
    sr = supply_response(ann)
    lib, bt = error_library(ann)
    b = bt.pop("_frame")
    bt.pop("_k")
    k = sr["g_revpar"]["same_year"] + sr["g_revpar"]["next_year"]
    bt["mae_supply_adj"] = float(np.mean(np.abs(b["actual"] - (b["cagr"] + k * b["excess"]))))
    sh = shock_stats(ann)
    events = yaml.safe_load((CONFIG_DIR / "event_analogs.yaml").read_text())["events"]
    per_event, pooled = event_analogs(monthly, events)
    fit = {"supply": sr, "errors": lib, "shocks": sh, "events": pooled, "backtest": bt}
    reg_path = CONFIG_DIR / "calibration.yaml"
    reg = yaml.safe_load(reg_path.read_text())
    reg = apply_to_registry(reg, fit, snapshot)
    print(json.dumps({"supply": sr, "shocks": sh, "backtest": bt, "events": pooled}, indent=1, default=float)[:4000])
    if a.dry_run:
        return 0
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    der = a.data_dir / "derived"
    der.mkdir(exist_ok=True)
    (der / "event_analogs.json").write_text(json.dumps(per_event, indent=1) + "\n")
    L = ["# Event analogs (private: measured on licensed CoStar data)", "",
         f"Snapshot {snapshot}. Event months against the median of the same year's other months (adjacent months excluded), "
         "log differences, so +0.10 is about +10%.", "",
         "| Event | Submarkets | Occupancy | ADR | ADR month before | ADR month after |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for e in per_event:
        md = e["median"]
        L.append(f"| {e['name']} | {e['n_submarkets']} | {md['occ']:+.3f} | {md['adr']:+.3f} | "
                 f"{md.get('adr_month_before', float('nan')):+.3f} | {md.get('adr_month_after', float('nan')):+.3f} |")
    (der / "event_analogs.md").write_text("\n".join(L) + "\n")
    print(f"wrote {reg_path} and {der}/event_analogs.*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
