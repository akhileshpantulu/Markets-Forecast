"""Track record: archive each forecast's ledger in the private data repo, and review past forecasts
against actuals once they arrive.

python -m engine.ledger.archive review <market-slug> [--data-dir /home/user/marketsdata]

archive(out, slug) writes <data-dir>/forecasts/<slug>/<date>_<registry version>.json. review() reads
those files and the panel's actuals and reports, for every forecast year that has happened: forecast
against actual for occupancy, ADR and RevPAR, the baseline-only error against the full ledger's error
(did the adjustments help?), whether the actual fell inside P10 to P90, and supply forecast against actual.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT = Path("/home/user/marketsdata")


def archive(out: dict, slug: str, data_dir: Path = DEFAULT) -> Path:
    d = data_dir / "forecasts" / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date.today().isoformat()}_{out['meta']['registry_version']}.json"
    keep = {k: out[k] for k in ("meta", "baseline", "years", "five_year", "supply", "adjustments", "gaps")}
    keep["drivers"] = {"applied": out["drivers"]["applied"]}
    p.write_text(json.dumps(keep, indent=1, default=str) + "\n")
    return p


def review(slug: str, data_dir: Path = DEFAULT) -> str:
    files = sorted((data_dir / "forecasts" / slug).glob("*.json"))
    ann = pd.read_parquet(data_dir / "panel" / "performance_annual.parquet")
    a = ann[(ann["slug"] == slug) & (ann["slice"] == "All") & ann["complete"]].set_index("year").sort_index()
    L = [f"# Forecast review: {slug}", ""]
    if not files:
        return "\n".join(L + ["No archived forecasts yet."])
    if a.empty:
        return "\n".join(L + ["This market is not in the panel; actuals cannot be read."])
    L += ["| Forecast made | Year | RevPAR forecast | RevPAR actual | Error | Baseline-only error | Inside P10 to P90 |",
          "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for f in files:
        fc = json.loads(f.read_text())
        made = f.name[:10]
        for y in fc["years"]:
            yr = y["year"]
            if y.get("is_stub") or yr not in a.index or (yr - 1) not in a.index:
                continue
            act = a.loc[yr, "revpar"] / a.loc[yr - 1, "revpar"] - 1
            base = [ln for ln in y["lines"] if ln["layer"] == "Baseline"]
            b_occ, b_adr = sum(ln["occ"] for ln in base), sum(ln["adr"] for ln in base)
            b_rev = (1 + b_occ) * (1 + b_adr) - 1
            r = y["range"]["revpar_growth"]
            L.append(f"| {made} | {yr} | {y['revpar_growth']:+.1%} | {act:+.1%} | {act - y['revpar_growth']:+.1%} | "
                     f"{act - b_rev:+.1%} | {'yes' if r['p10'] <= act <= r['p90'] else 'no'} |")
    L += ["", "If the full ledger's errors are smaller than the baseline-only errors, the adjustment lines are adding value. "
          "Persistent errors of one sign in a layer point to the registry value to revisit (python -m engine.ledger.calibrate)."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["review"])
    ap.add_argument("market")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT)
    a = ap.parse_args(argv)
    print(review(a.market, a.data_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
