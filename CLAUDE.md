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
| Pipeline probabilities (2026-11-06) | Ladder by stage: under construction 90%, final planning 60%, planning 50%, proposed 40%; renovation reopenings use 90%; deferred 10%. Delays, removals and induced-demand shares remain assumptions. |
| Band widening (2026-11-06) | Approved: widen later horizons using the backtest. This only ever widens, and never makes a year narrower than the year before. |
| Drivers raising shock probability (2026-11-06) | Approved: `affects: shock_probability` with a multiplier on the market's own shock rate. |
| Event probability ladder (2026-11-06) | Approved: rumour 10% to 25%, credible report 30% to 50%, announced 70% to 90%, tickets on sale 85% to 95%, held 100%, cancelled 0% (`config/driver_priors.yaml`). |
| Adjustable assumptions (2026-11-06) | The web page has an Assumptions tab for weights and biases. Every change is reported as a user adjustment. |
| Baseline basis (2026-11-06) | The baseline is the market's long-run CAGR over its whole history: RevPAR = occupancy x ADR, and demand = supply + occupancy. It is adjusted for drivers, supply against its trend, inflation against its history, shocks and momentum. The fitted normal-year trend stays as an alternative setting and is always reported as a sensitivity. |
| RevPAR identity (2026-11-06) | RevPAR growth = occupancy growth combined with ADR growth. The reported RevPAR P50 is (1 + occupancy P50) x (1 + ADR P50) - 1, and the RevPAR level P50 is occupancy P50 x ADR P50. The median of simulated RevPAR stays in the output as `p50_paths`. Bands (P10 to P90) are from the simulated RevPAR. |
| Pricing rule (2026-11-06) | Occupancy and rate trade off ("both" forms). In normal years ADR moves inversely to occupancy change beyond trend (`occ_change_response`, assumption -0.25; history shows a positive slope, so it is a judgement). Rate growth is dampened by last year's occupancy above the market's normal level (fitted, shrunk, never positive) and more steeply above 90% (assumption -0.5). Downturns and recoveries still pass through to rate at the fitted coefficient. The previous rule is always run as a sensitivity. |
| Near-term inflation (2026-11-06) | The current and next years come from official near-term forecasts in `config/macro/near_term/<ISO2>.yaml`, sourced and dated, refreshed by `/forecast`. IMF fills later years. UK: OBR calendar-year CPI (the Bank of England publishes Q4 rates only). France: Banque de France HICP. |
| Ledger forecast (2026-11-09) | The central forecast is a ledger: baseline (long-run RevPAR CAGR, occupancy flat by default) plus named lines for supply, supply quality and drivers, each with its reason and basis. A simulation produces only the ranges. Coefficients live in `config/calibration.yaml`, fitted on the European panel where possible, adjustable on the page, and refined as the track record grows. Drivers are sized monthly and rolled up to the year. |
| Supply quality (2026-11-09) | The tier (CoStar chain scale) and product type of new, reopening and closing hotels move market rate and occupancy through mix, and new rooms compete less the further their tier sits from the market's average. Applies to both rate and occupancy. |
| Site (2026-11-09) | Slimmed to four tabs: Forecast, Supply, Assumptions, Data. The page redeploys automatically on every push. |
| Current year (2026-11-09) | The current (stub) year grows at its year-to-date YoY rate: occupancy and ADR for Jan to the last actual month against the same months of last year. Events in the remaining months that differ from last year (new ones, or last year's that do not recur) are separate driver lines with `timing.months`. |
| Research depth (2026-11-09) | /forecast runs four researchers (events, demand, supply, geopolitics) against `config/research_checklist.yaml` (45 items). Every item is answered in `runs/<market>/coverage_<owner>.yaml` with cards or a sourced nil finding; `engine.drivers.doc` reports unanswered items as errors and prints a coverage table in drivers.md. Method: `config/research_method.md` (three searches per item including the local language, primary sources, orders of effect, calculators). |
| Research in Cowork (2026-11-10) | The user may run the driver research in Claude Cowork (browser access) instead of /forecast. The page offers a market-specific research brief (Assumptions tab, "Download research brief"); Cowork saves one `drivers-<slug>.yaml` with `drivers` and `coverage`; the user drops it into the Drivers slot and the page checks cards and coverage. Generic version: `cowork/hotel-market-research/SKILL.md`, generated by `python -m engine.drivers.brief` from the checklist (a test keeps it in sync). |
| Long-run CAGR window (2026-11-10) | The baseline CAGR always starts at the earliest complete year of data provided and ends at the last complete year outside COVID. Confirmed data breaks are adjusted (spliced levels) so they do not count as growth; the baseline line also states the raw-data CAGR. A start year on the Assumptions tab overrides it. |
| Supply source (2026-11-06) | The pipeline drives supply but is not the only source. Supply never falls below the long-run supply trend, from the first forecast year. Only pipeline rooms above the trend bring extra demand. |

## Architecture decisions

- **One engine, two runtimes.** The Python package `engine/` runs locally and in the browser through Pyodide 314.0.7. `requirements.txt` pins numpy, pandas, scipy, statsmodels, pydantic, pyyaml, pyarrow and jinja2 to the versions bundled with that Pyodide release, so the numbers are the same in both places. openpyxl is pure Python and gets loaded with micropip.
- Core functions take bytes or DataFrames. Disk I/O lives in thin CLI modules such as `engine/ingest/run.py`. Browser code calls the core directly.
- **Guardrail layer.** Every estimator takes its history from `engine/history/sample.py` (`build_growth_sample`, `usable`) and passes its fit through `influence_check`. Never fit on raw annual growth directly. `engine/history/anomalies.py` finds persistent uniform level breaks: 12 months moving the same way by more than 25%, with no reversal afterwards and no opposite move before. Confirmed breaks are excluded from growth estimation automatically and pre-break levels are spliced. Steps too recent to confirm are flagged for review and kept. Users override per break in `estimation.series_breaks`. The user approved this automatic rule on 2026-11-06.
- **Web page.** `web/` (Vite + TypeScript + ECharts) is a drag-and-drop page. The Python engine runs in a Web Worker through Pyodide, and on the main thread when `forecast-app.html` is opened from disk. The browser entry point is `engine/web_api.py`. The site contains code only; `web/scripts/check-dist.mjs` fails a build that contains data. `web/tests/e2e.mjs` drives both builds in Chromium.
- **Ledger forecast (`engine/ledger/`)** is the forecast engine (the CLI and the page). `run.py` builds each year as lines in three layers (baseline, supply volume and quality, drivers); `calculators.py` sizes drivers (event, air_capacity, source_market, price_change, competing_supply) with written-out working; `registry.py` reads `config/calibration.yaml`; `calibrate.py` fits the registry on the private panel; `archive.py` stores forecasts in the private repo and reviews them against actuals; `report.py` writes forecast.md. The older stochastic engine (`engine/forecast.py` and its modules) is kept as reference and still tested, but nothing user-facing calls it.
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
- Guardrails for anomalies: done.
- Phase B (history, stub year, baseline demand and ADR, backtest): done. Backtest reviewed by the user.
- Phase C (supply pipeline, reopenings, removals, ramp-up, induced demand, unannounced supply): done.
- Phase D core: done. It covers:
  - the driver card schema and the event-status ladder
  - application in the simulation: one-off with payback, temporary, step, and shock probability
  - automated critic checks and attribution by removal
  - agent definitions in `.claude/agents/` and the `/forecast` command in `.claude/commands/`
- Phase D research: run for London (8 proposed cards, local only). Not yet run for Paris.
- `/forecast <market>` produces a drivers document only: researchers write `runs/<market>/drivers_<agent>.yaml` in parallel, the critic sets status, and `python -m engine.drivers.doc <market>` merges and checks them into `drivers.yaml` (for the page's Drivers slot) and `drivers.md` (readable). The forecast itself runs on the page. CoStar exports or the page's results.json are optional inputs that let the critic check overlap with the baseline.
- Ledger rebuild (2026-11-09): done. Calibrated on the European panel (snapshot 2026-09): supply response, CAGR-rule error library (ranges), downturn rate and depth, event analogs.
- Web page: Forecast, Supply, Assumptions, Data. Upload slots: performance, pipeline, drivers.
- Not yet built: scenarios with explicit paths, the Excel output, class-level calibration of the tier indices (needs class exports).

## Ledger: how a year is built

- Current year: year-to-date YoY growth, plus driver lines for remaining-month events that differ from last year.
- Layer 1, baseline: long-run RevPAR CAGR from the earliest complete year of data to the last non-COVID year, on break-adjusted levels. Occupancy flat by default, rate carries it; option to split by each CAGR. Inflation line: pass-through x (forecast inflation - historical average); near-term from `config/macro/near_term/`.
- Layer 2, supply: inventory growth against its long-run trend x fitted coefficients (same year and next year); reopenings of temporarily closed hotels (rooms return, a share of demand returns); tier and product mix of rooms opening and closing (arithmetic blend of tier rate and occupancy indices against the market's average tier); competitive overlap (new rooms compete `overlap_decay`^steps as directly). Supply floor: inventory never grows below its long-run trend.
- Layer 3, drivers: each card's expected effect = chance x mean size x timing profile (one-off pays back next year). Calculators size cards from stated inputs; monthly effects roll up with the market's monthly demand and revenue shares. Shock-probability cards use the panel's downturn rate and depth.
- Ranges: the CAGR rule's historical miss (panel error library by horizon, Gaussian copula, centred), scaled to the market's own spread of annual growth (shrunk to the European norm), plus pipeline and driver draws. The central number is the ledger; RevPAR = (1 + occupancy) x (1 + ADR) - 1.
- Track record: `python -m engine <market> --archive` stores the forecast in `marketsdata/forecasts/`; `python -m engine.ledger.archive review <market>` compares with actuals once the panel has them.
- Recalibrate after a panel update: `python -m engine.ledger.calibrate` (needs `/home/user/marketsdata`). Only pooled values go into the public registry; per-event and per-submarket results go to `marketsdata/derived/`.

## Drivers and settings

- Driver cards: `engine/drivers/schema.py`.
  - `affects` is one of demand, adr, supply or shock_probability.
  - Shape: `one_off` lifts one year and the next year pays it back; `temporary` decays over `decay_years`; `step` is permanent.
  - Each card has its own random stream, so adding a card never changes the baseline draws.
- Probability comes from the card, or from `event_status` through the ladder. Cards with validation errors are not applied. Proposed cards are included while `include_proposed` is true, and the run is then provisional.
- Settings: `engine/settings.py` covers pipeline chance and delay by stage, removals, induced demand, unannounced supply, demand trend and ADR drift biases, the occupancy pricing rule (change response, level dampening, 90% threshold, downturn pass-through), shock multipliers, inflation path, band width, horizon widening and the ceiling. They are applied after fitting. The backtest is not affected, and is cached across setting changes.
- CLI: `python -m engine <market> --drivers <yaml> --settings <json>`. Without `--drivers`, the default is `runs/<market>/drivers.yaml` if present.

## Private data panel (akhileshpantulu/marketsdata)

- Licensed CoStar monthly performance for European submarkets lives in the private repo `akhileshpantulu/marketsdata` (snapshot 2026-09: 303 submarkets, 79 markets, 21 countries, performance 1996 to Sep 2026, EUR at constant current rates; no UK; no CoStar forecast figures).
- In a new session: `add_repo` it, clone to `/home/user/marketsdata`, read `panel/CATALOG.md`. Read it with `engine/panel.py` (`load_panel`, `submarket_monthly(slug)`); `MARKETS_DATA_DIR` overrides the path.
- The user updates it from the browser: upload an export to `inbox/` on GitHub; the repo's build-panel action archives it in `raw/` and rebuilds `panel/` (newer snapshots win per geography, slice and month).
- Never copy panel data into this public repo or the web build.

## External public data

- The user supplied API connectors (Eurostat tourism and air passengers, Eurostat HICP accommodation, IMF WEO GDP and CPI, ECB FX, INE Spain). They are listed verbatim in `config/external_sources.yaml`.
- This session's network blocks those hosts, so `.github/workflows/external-data.yml` fetches them on GitHub's runners on push, every Monday, or on demand. It commits raw responses plus provenance (`*.meta.json`: URL, fetch time, status, SHA-256) to `data/external/`.
- Near-term inflation overrides: `config/macro/near_term/<ISO2>.yaml` (merged by `engine.forecast.apply_near_term`). Each entry records publisher, title, URL, published, accessed and how it was verified. The session network blocks the official hosts, so the 2026-11-06 entries are search-snippet level and say so.
- `python -m engine.external.build` turns them into `config/macro/generated/<ISO2>.yaml`. That file holds the CPI history and IMF projections; years after the fetch year minus one count as projections, which is a labelled assumption. The engine prefers this file over the manual `config/macro/<ISO2>.yaml`.
- The workflow pushes to the working branch. Run `git pull --rebase` before pushing.
- These are public statistics. They may be committed and bundled into the web page. CoStar data still may not.

## Model notes (Phases B and C)

- Engine core is numpy and pandas only (no scipy or statsmodels) so the browser loads less.
- Demand: AR(1) on normal-year log growth. Normal years exclude shock years, their recovery years (up to 2), COVID years (default), break years and partial years. Shrinkage toward `config/priors.yaml` with precision weights.
- Shocks: a year falling more than 2 robust standard deviations below the median. "Severe" is a log fall beyond 0.5. Profiles come from history. Annual probabilities are Beta priors updated with the shock count.
- ADR pricing rule (`engine/adr/model.py`): pricing = beta x shock part of the occupancy change + beta_change x the rest (beyond trend) + beta_level x (last year's occupancy - normal) + beta_high x max(last year's occupancy - 90%, 0). The shock part is the demand shock deviation of that year. Normal level = average occupancy over the anchor window, COVID years excluded.
- ADR: CPI mode when inflation history and projections are available (from `config/macro/generated/`, fetched by the workflow). Otherwise it runs in nominal mode and the gap is recorded. The backtest forecasts inflation naively at each origin: the last observed rate decays to 2%.
- Stub year: errors are calibrated on how the same rule performed in past years at the same month cutoff.
- Long-run anchor (`engine/history/anchor.py`):
  - CAGRs run from the first to the last complete year, on spliced levels.
  - Demand trend = demand CAGR minus the expected shock drag.
  - ADR drift = ADR CAGR, in real terms in CPI mode.
  - Pricing power acts on occupancy change beyond occupancy's own CAGR.
  - The backtest anchors at each origin on data up to that origin. It also reports the fitted-trend basis for comparison.
  - `explanation.reconciliation` walks from the long-run RevPAR CAGR to the forecast year by year, and the rows add up exactly.
- Supply realism (added after the user flagged aggressive growth in another market; the floor rule replaced the phase-in on 2026-11-06):
  - Future months follow the recent seasonal pattern of available rooms, so markets with seasonal closures are handled.
  - Supply floor: monthly rooms = max(long-run supply trend from today's level, stock less removals plus pipeline). The difference is reported as "unannounced". Pipeline rooms above the trend induce demand at the new or reopened share; trend-level rooms do not, because their demand is in the anchor. The Assumptions tab sets the trend share for years 1 and 2.
  - Without this, a thin pipeline lets the historical demand trend push occupancy up to the ceiling.
- Explanation (`engine/explain.py`): the projection traces every component path by path.
  - The growth bridge splits mean log growth into demand trend, momentum, shocks, new-hotel demand and ceiling, minus supply (existing stock, removals, new builds, reopenings, unannounced); and ADR into inflation, real drift, downturn pass-through, occupancy change, occupancy level and carry-over. The parts add up exactly.
  - Year-by-year text uses paths without a shock, which is what the P50 reflects.
  - Plausibility flags: ceiling share, supply against history, P50 outside history P5 to P95, occupancy above the historical maximum, real ADR above 3% for three years, and five-year cumulative RevPAR against the market's own five-year periods.
- The backtest conditions on realised supply, picks residual type and COVID treatment by CRPS, and calibrates spread only if pooled ex-COVID P10 to P90 coverage falls outside 70% to 90%.

## Commands

- `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- `python -m pytest`
- `python -m engine.ingest <market-slug> [--no-move] [--inbox PATH]`
- `python -m engine <market-slug> [--inbox PATH] [--drivers YAML] [--settings JSON] [--archive]`: ingest, validate, ledger forecast; writes validation.md, forecast.md, results.json
- `python -m engine.ledger.calibrate [--data-dir PATH] [--dry-run]`: refit config/calibration.yaml on the private panel
- `python -m engine.ledger.archive review <market-slug>`: archived forecasts against actuals
- `cd web && npm ci && npm run build:all` builds `web/dist` (site) and `web/dist/forecast-app.html` (single file)
- `cd web && PYODIDE_DIR=<local pyodide> node tests/e2e.mjs` runs the browser end-to-end test (omit `PYODIDE_DIR` to use the CDN)
- `/forecast <market-slug>`: research, critic and drivers document for one market
- `python -m engine.drivers.doc <market-slug> [files...] [--stub-year YYYY]`: merge `runs/<market>/drivers_*.yaml`, check, write drivers.yaml and drivers.md
