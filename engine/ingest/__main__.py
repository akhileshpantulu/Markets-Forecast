"""CLI: python -m engine.ingest <market-slug> [--no-move] [--inbox PATH]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine.ingest.run import ingest_market


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest and validate CoStar uploads for one market.")
    ap.add_argument("market")
    ap.add_argument("--no-move", action="store_true", help="leave raw files in inbox/ after processing")
    ap.add_argument("--inbox", type=Path, default=None, help="read uploads from this folder instead of inbox/<market>/")
    args = ap.parse_args(argv)
    res, report = ingest_market(args.market, move=not args.no_move, inbox=args.inbox)
    print(f"run_id: {res.run_id}")
    print(f"validation report: {report}")
    print("status:", "passed" if res.passed else "FAILED")
    for group, checks in res.checks.items():
        for c in checks:
            if c.status in ("warn", "fail"):
                print(f"  [{c.status}] {group} {c.id} {c.title}: {c.message}")
    for e in res.fatal:
        print(f"  [fatal] {e}")
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
