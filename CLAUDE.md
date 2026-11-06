# CLAUDE.md

Standing rules for every Claude Code session in this repository. The full
specification is in `BUILD_PROMPT.md`; this file records the rules, the user's
decisions, and the current build state.

## What this is

A stochastic forecasting application for hotel markets. CoStar exports go in;
out come probability-weighted forecasts of occupancy, ADR and RevPAR growth for
the stub year plus 5 full years, along with a sourced memo, a one-tab Excel file
and a web view.

## Operating rules

- Code computes the numbers. Never type a forecast figure that did not come from the simulation.
- Never invent data. If something is missing, record it as a gap and use an explicitly labelled assumption.
- Never assume the current status of a geopolitical event, route, event or policy from training data. Search at run time and date-stamp the result.
- Every factual claim carries a source: URL, publisher, publication date, access date. Prefer primary sources. Event status taken from news needs two independent sources.
- Keep facts and assumptions visibly separate in every output.
- Use a fixed random seed per run and record it in the run metadata.
- When a methodological choice materially changes the result, run both versions and report the difference.
- Commit in small steps. Run `python -m pytest` before every commit.

## Writing style for all generated prose

Direct sentences. No em dashes. No filler. No promotional language.
Percentages to one decimal place; occupancy points to one decimal place.

## Data licensing (hard rule)

- Raw CoStar data is licensed. It never leaves the private repository and is never deployed to a web page.
- **The repository is currently PUBLIC** (verified 2026-11-06 through the GitHub search API).
  Until it is private, nothing under `inbox/`, `data/raw/`, `data/processed/` or `runs/`
  may be committed. `.gitignore` and `tests/test_repo_guard.py` enforce this.
  After the user makes the repo private, set `repo_private: true` in `config/hosting.yaml`,
  then remove the guard block from `.gitignore`.
- Spreadsheet, CSV and Parquet files may only be tracked under `tests/fixtures/`, and those are synthetic.
- The web build contains code only. It holds no CoStar rows and no run outputs. Dropped files are processed in the browser and never uploaded.

## Decisions recorded from the user (2026-11-06)

| Question | Answer |
| --- | --- |
| Pilot market | Paris, Louvre/Marais submarket (`paris-louvre-marais`). The sample exports were provided. |
| Performance export | Monthly CoStar "DataTable" export at submarket level. Performance data runs Jan 1998 to Sep 2026; 1996 and 1997 rows carry inventory only. |
| Pipeline export | CoStar property export ("CostarExport"). It has construction status, projected open (Year/Month Built for new builds, Year/Month Renov for reopenings) and construction start. It has no historical snapshots, so the pipeline priors are assumptions. |
| Reference series | The user will upload a France country-level export, into `inbox/<market>/reference/`. |
| Hosting | The repo stays under the personal account `akhileshpantulu`, so private GitHub Pages is not available. The user wants a hosted page (GitHub Pages or Vercel) that accepts dropped files and shows the output, plus a local HTML file that does the same. This is built as a client-side-only page, so it can be public. |
| Upload slots | Market performance, pipeline, airline/airport data, and country performance. Airline and country slots stay disabled until the user provides example files. |
| Year basis | Calendar year, January to December. |
| Currency | Local currency of the export (EUR for Paris). The export does not state its currency, so it is set in the market config. |
| Airport and events | The user will upload both. Also use other web sources. |
| Exclusions | None stated. |

## Architecture decisions

- **One engine, two runtimes.** The Python package `engine/` runs locally and in the browser through Pyodide 314.0.7. `requirements.txt` pins numpy, pandas, scipy, statsmodels, pydantic, pyyaml, pyarrow and jinja2 to the versions bundled with that Pyodide release, so the numbers are the same in both places. openpyxl is pure Python and gets loaded with micropip.
- Core functions take bytes or DataFrames. Disk I/O lives in thin CLI modules such as `engine/ingest/run.py`. Browser code calls the core directly.
- **Guardrail layer.** Every estimator takes its history from `engine/history/sample.py` (`build_growth_sample`, `usable`) and passes its fit through `influence_check`. Never fit on raw annual growth directly. `engine/history/anomalies.py` finds persistent uniform level breaks: 12 months moving the same way by more than 25%, with no reversal afterwards and no opposite move before. Confirmed breaks are excluded from growth estimation automatically and pre-break levels are spliced. Steps too recent to confirm are flagged for review and kept. Users override per break in `estimation.series_breaks`. The user approved this automatic rule on 2026-11-06.
- **Web page.** `web/` (Vite + TypeScript + ECharts) is a drag-and-drop page. The Python engine runs in a Web Worker through Pyodide, and on the main thread when `forecast-app.html` is opened from disk. The browser entry point is `engine/web_api.py`. The site contains code only; `web/scripts/check-dist.mjs` fails a build that contains data. `web/tests/e2e.mjs` drives both builds in Chromium.
- The research agents (drivers, critic, memo) run in Claude Code sessions, not in the browser. The hosted page runs the deterministic steps: ingest, history, baseline, supply, simulation, backtest, and what-if analysis. It applies driver and scenario files that come from a run.

## Internal schema

Performance (monthly, `data/processed/<market>/performance_monthly.parquet`):
`market, period_start, period_type, supply, demand, occupancy, adr, revpar, revenue, source_file`.
Extra columns: `inventory_rooms, existing_buildings, under_construction_rooms, under_construction_buildings, delivered_rooms_12m, opened_rooms_12m, days_in_period, available_rooms, closed_rooms`.
Supply is available room nights. It excludes temporarily closed hotels, so `closed_rooms = inventory_rooms - supply / days`.

Annual (`performance_annual.parquet`): supply, demand and revenue are summed, and occupancy, ADR and RevPAR are recomputed from the sums. There is also a `complete` flag.

Pipeline (`pipeline.parquet`): `project_id, name, rooms, stage, projected_open, brand, chain_scale, source_file, snapshot_date`, plus `stage_raw, project_type (new_build | renovation_reopening | renovation_open | expansion), in_current_supply, projected_open_basis, operation_status, scale, market, submarket, constr_start, expansion_rooms`.

Column mappings are in `data/mappings/*.yaml`. If a mapping is ambiguous, ingestion stops and the user is asked.

## Sourcing standard

Each evidence item records `claim, url, publisher, published, accessed`. Primary sources come first: airport operators, statistics agencies, central banks, event organisers, and company filings. News alone is not enough for event status; it needs two independent sources.

## Data findings needing user decisions

- **ADR level break in 1999.** Every month of 1999 shows ADR between 14% and 76% above 1998 (median +53%), and 2000 is flat. The guardrail detects this and excludes the 1999 ADR growth observation automatically. Pre-1999 ADR is spliced for level statistics.
- **Closed rooms.** 730 rooms are out of supply in Sep 2026 and match exactly the four temporarily closed renovation projects in the pipeline. They return to supply on reopening.

## Build state

- Phase A (scaffold, ingestion, validation): done.
- Guardrails for anomalies: done, ahead of Phase B.
- Drag-and-drop page: built early. It covers ingestion, validation, guardrails, pipeline and mapping views. Forecast views arrive with Phases B to G. Airline and country slots are waiting for example files.
- Phases B to H: not started.

## Commands

- `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- `python -m pytest`
- `python -m engine.ingest <market-slug> [--no-move] [--inbox PATH]`
- `cd web && npm ci && npm run build:all` builds `web/dist` (site) and `web/dist/forecast-app.html` (single file)
- `cd web && PYODIDE_DIR=<local pyodide> node tests/e2e.mjs` runs the browser end-to-end test (omit `PYODIDE_DIR` to use the CDN)
- `/forecast <market-slug>`: orchestration command, built in Phase D.
