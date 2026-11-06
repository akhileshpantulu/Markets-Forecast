"""CLI: python -m engine <market-slug> [--inbox PATH] [--move] [--paths N]

Runs ingestion, validation, guardrails and the baseline forecast, and writes
runs/<market>/<run_id>/: validation.md, history.md, backtest.md, baseline.md, results.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from engine.config import REPO_ROOT, load_market_config
from engine.forecast import load_inputs, run_forecast
from engine.ingest.run import ingest_market
from engine.outputs.markdown import render_backtest, render_baseline, render_history


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest, validate and run the baseline forecast for one market.")
    ap.add_argument("market")
    ap.add_argument("--inbox", type=Path, default=None)
    ap.add_argument("--move", action="store_true", help="archive raw files to data/raw after a passing run")
    ap.add_argument("--paths", type=int, default=None)
    ap.add_argument("--drivers", type=Path, default=None,
                    help="driver cards (YAML list or mapping with 'drivers'); default runs/<market>/drivers.yaml if present")
    ap.add_argument("--settings", type=Path, default=None, help="JSON of user settings (as downloaded from the Assumptions tab)")
    args = ap.parse_args(argv)
    res, report = ingest_market(args.market, move=args.move, inbox=args.inbox)
    print(f"run_id: {res.run_id}\nvalidation: {report}")
    if not res.passed:
        print("validation FAILED; no forecast")
        return 1
    cfg = load_market_config(args.market)
    priors, ppri, macro = load_inputs(cfg)
    import yaml
    dpath = args.drivers or (REPO_ROOT / "runs" / args.market / "drivers.yaml")
    drivers = []
    if dpath.exists():
        doc = yaml.safe_load(dpath.read_text(encoding="utf-8"))
        drivers = doc.get("drivers", []) if isinstance(doc, dict) else (doc or [])
        print(f"drivers: {len(drivers)} cards from {dpath}")
    settings = {}
    if args.settings:
        sdoc = json.loads(args.settings.read_text(encoding="utf-8"))
        settings = sdoc.get("settings", sdoc)
    out = run_forecast(res, cfg, priors, ppri, macro, n_paths=args.paths, drivers=drivers, settings=settings)
    run_dir = REPO_ROOT / "runs" / args.market / res.run_id
    (run_dir / "history.md").write_text(render_history(out, cfg), encoding="utf-8")
    (run_dir / "backtest.md").write_text(render_backtest(out), encoding="utf-8")
    (run_dir / "baseline.md").write_text(render_baseline(out, cfg), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({"run_id": res.run_id, "date": date.today().isoformat(), **out},
                                                     allow_nan=False, indent=1), encoding="utf-8")
    print(f"outputs: {run_dir}")
    for r in out["summary"]:
        print(f"  {r['year']} {r['label']:<9} occ {r['occ_growth']['p50'] * 100:+5.1f}%  ADR {r['adr_growth']['p50'] * 100:+5.1f}%  "
              f"RevPAR {r['revpar_growth']['p50'] * 100:+5.1f}% (P50)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
