"""CLI: python -m engine <market-slug> [--inbox PATH] [--move] [--paths N] [--drivers YAML] [--settings JSON] [--archive]

Ingests and validates the CoStar exports, runs the ledger forecast (engine/ledger), and writes
runs/<market>/<run_id>/: validation.md, forecast.md (the ledger with every line's reason) and results.json.
--archive also stores the forecast in the private data repo for the track record (engine/ledger/archive.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from engine.config import REPO_ROOT, load_market_config
from engine.forecast import load_inputs
from engine.ingest.run import ingest_market
from engine.ledger.archive import DEFAULT as DATA_DIR, archive
from engine.ledger.report import render
from engine.ledger.run import run_ledger


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest, validate and run the ledger forecast for one market.")
    ap.add_argument("market")
    ap.add_argument("--inbox", type=Path, default=None)
    ap.add_argument("--move", action="store_true", help="archive raw files to data/raw after a passing run")
    ap.add_argument("--paths", type=int, default=4000)
    ap.add_argument("--drivers", type=Path, default=None,
                    help="driver cards (YAML list or mapping with 'drivers'); default runs/<market>/drivers.yaml if present")
    ap.add_argument("--settings", type=Path, default=None, help="JSON of user settings (as downloaded from the Assumptions tab)")
    ap.add_argument("--archive", action="store_true", help="store the forecast in the private data repo for the track record")
    args = ap.parse_args(argv)
    res, report = ingest_market(args.market, move=args.move, inbox=args.inbox)
    print(f"run_id: {res.run_id}\nvalidation: {report}")
    if not res.passed:
        print("validation FAILED; no forecast")
        return 1
    cfg = load_market_config(args.market)
    _, ppri, macro = load_inputs(cfg)
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
    out = run_ledger(res, cfg, ppri, macro, drivers=drivers, settings=settings, n_paths=args.paths)
    run_dir = REPO_ROOT / "runs" / args.market / res.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "forecast.md").write_text(render(out, cfg.name), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({"run_id": res.run_id, "date": date.today().isoformat(), **out},
                                                     allow_nan=False, indent=1, default=str), encoding="utf-8")
    print(f"outputs: {run_dir}")
    for y in out["years"]:
        r = y["range"]["revpar_growth"]
        print(f"  {y['label']:<26} occ {y['occ_growth'] * 100:+5.1f}%  ADR {y['adr_growth'] * 100:+5.1f}%  RevPAR {y['revpar_growth'] * 100:+5.1f}%"
              f"  (P10 {r['p10'] * 100:+.1f}%, P90 {r['p90'] * 100:+.1f}%)")
    if args.archive:
        print(f"archived: {archive(out, args.market, DATA_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
