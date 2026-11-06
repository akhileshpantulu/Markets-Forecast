"""Render validation.md from an IngestResult."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from engine import __version__
from engine.config import MarketConfig
from engine.ingest.core import IngestResult

STATUS_LABEL = {"pass": "Pass", "warn": "Warning", "fail": "FAIL", "info": "Info"}


def _pct(x) -> str:
    return "" if pd.isna(x) else f"{x * 100:.1f}%"


def _s(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NA else str(x)


def _num(x, d=0) -> str:
    return "" if pd.isna(x) else f"{x:,.{d}f}"


def _guardrail_section(res: IngestResult) -> list[str]:
    L = ["## Estimation guardrails", "",
         "These rules decide which history the model may fit on. They run automatically on every upload.", ""]
    if res.breaks:
        L += ["**Level breaks**", ""] + [f"- {b.describe()}" + (f" {b.note}" if b.note else "") for b in res.breaks]
        L += ["", "Levels before a confirmed break are rescaled by the step factor in "
              "`performance_monthly_spliced` so historical ranges compare like with like. "
              "Displayed history keeps the original values.", ""]
    else:
        L += ["No level breaks detected.", ""]
    g = res.growth_sample
    L += ["**Annual growth observations used for estimation**", "",
          "| Year | Demand | Occupancy | ADR | Excluded because |", "| --- | --- | --- | --- | --- |"]
    for r in g.itertuples():
        def cell(m):
            v = getattr(r, f"g_{m}")
            mark = "" if getattr(r, f"use_{m}") else " (out)"
            return "" if pd.isna(v) else f"{np.expm1(v) * 100:+.1f}%{mark}"
        reasons = sorted({getattr(r, f"reason_{m}") for m in ("demand", "occupancy", "adr")} - {""})
        L.append(f"| {r.year} | {cell('demand')} | {cell('occupancy')} | {cell('adr')} | {'; '.join(reasons)} |")
    L += ["", "**Influence check** (leave-one-out, on the mean of usable growth)", ""]
    any_flag = False
    for m, inf in res.influence.items():
        for f in inf.flagged:
            any_flag = True
            L.append(f"- {m}: {f}")
    if not any_flag:
        L.append("- No single year moves the mean more than 3 times as much as a typical year.")
    else:
        L += ["", "Flagged years stay in the sample. Phase B estimators fit with and without them and report both."]
    L.append("")
    return L


def render_validation(res: IngestResult, cfg: MarketConfig, today: date | None = None) -> str:
    today = today or date.today()
    L: list[str] = []
    status = "PASSED" if res.passed else "FAILED: the run stops here"
    L += [f"# Validation report: {cfg.name}", "",
          f"- Run ID: `{res.run_id}`",
          f"- Date: {today.isoformat()}",
          f"- Engine version: {__version__}",
          f"- Currency: {cfg.currency} (from market config; the export does not state it)",
          f"- Status: **{status}**", ""]

    if res.fatal:
        L += ["## Stopping errors", ""] + [f"- {e}" for e in res.fatal] + [""]

    L += ["## Files", "", "| Folder | File | Mapping | Sheet | Rows | Snapshot date | SHA-256 |",
          "| --- | --- | --- | --- | ---: | --- | --- |"]
    for f in res.files:
        L.append(f"| {f.category} | {f.filename} | {f.mapping or f.note} | {f.sheet or ''} | {f.rows} | "
                 f"{f.snapshot_date or ''} | `{f.sha256[:12]}` |")
    L.append("")

    L += ["## Column mapping", ""]
    for f in res.files:
        if not f.mapping:
            continue
        L += [f"### {f.filename} ({f.mapping})", "", "| Source column | Internal field |", "| --- | --- |"]
        L += [f"| {src} | `{dst}` |" for src, dst in f.mapped]
        L += ["", f"Not stored ({len(f.ignored)} columns): " + ", ".join(f.ignored) + "."]
        if f.duplicate_headers:
            L += ["", f"Repeated headers in the source, not mapped: {', '.join(f.duplicate_headers)}."]
        if f.other_sheets:
            L += ["", f"Other sheets not read: {', '.join(f.other_sheets)}."]
        L.append("")

    for group, checks in res.checks.items():
        if not checks:
            continue
        L += [f"## Checks: {group}", "", "| ID | Level | Result | Check | Finding |", "| --- | --- | --- | --- | --- |"]
        for c in checks:
            L.append(f"| {c.id} | {c.level} | {STATUS_LABEL[c.status]} | {c.title} | {c.message} |")
        L.append("")
        for c in checks:
            if c.details and c.status in ("warn", "fail", "info"):
                L += [f"**{c.id} {c.title}: details**", ""] + [f"- {d}" for d in c.details] + [""]

    if res.annual is not None:
        L += ["## Annual summary (derived from monthly data)", "",
              f"| Year | Months | Supply (room nights) | Demand (room nights) | Occupancy | ADR ({cfg.currency}) | RevPAR ({cfg.currency}) | Occ. growth | ADR growth |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        a = res.annual.sort_values("year").reset_index(drop=True)
        a["occ_g"] = a["occupancy"] / a["occupancy"].shift(1) - 1
        a["adr_g"] = a["adr"] / a["adr"].shift(1) - 1
        for r in a.itertuples():
            yr = f"{r.year}" + ("" if r.complete else " (partial)")
            L.append(f"| {yr} | {r.months_observed} | {_num(r.supply)} | {_num(r.demand)} | {_pct(r.occupancy)} | "
                     f"{_num(r.adr, 1)} | {_num(r.revpar, 1)} | {_pct(r.occ_g)} | {_pct(r.adr_g)} |")
        L += ["", "Growth for a partial year compares a part year with a full year and is not meaningful; "
              "the stub-year engine handles it in Phase B.", ""]

    if res.growth_sample is not None:
        L += _guardrail_section(res)

    if res.pipeline is not None and not res.pipeline.empty:
        L += ["## Pipeline as read", "",
              "| Project | Rooms | Stage | Type | Projected open | Basis | Class | Brand |",
              "| --- | ---: | --- | --- | --- | --- | --- | --- |"]
        for r in res.pipeline.sort_values("projected_open").itertuples():
            po = "" if pd.isna(r.projected_open) else f"{r.projected_open:%Y-%m}"
            L.append(f"| {r.name} | {r.rooms} | {r.stage} | {r.project_type} | {po} | {r.projected_open_basis} | "
                     f"{_s(r.chain_scale)} | {_s(r.brand)} |")
        L.append("")

    L += ["## Assumptions and gaps", "",
          f"- Assumption: values are in {cfg.currency}, the local currency of the export (confirmed by the user). "
          "The export has no currency field.",
          "- Assumption: for renovation projects, Year Renov and Month Renov are the projected reopening date.",
          "- Assumption: for new builds, Year Built and Month Built are the projected opening date.",
          "- Gap: the pipeline export has no historical snapshots, so stage conversion and delay priors in "
          "config/pipeline_priors.yaml are assumptions until snapshots are available."
          if res.pipeline is None or res.pipeline["snapshot_date"].nunique() <= 1 else
          "- Pipeline snapshots available for calibration.",
          "- Gap: no national or regional reference series uploaded yet." if res.reference_monthly is None else
          "- Reference series ingested.",
          ""]
    if res.notes_text:
        L += ["## User notes (optional/notes.txt)", "", res.notes_text.strip(), ""]
    return "\n".join(L)
