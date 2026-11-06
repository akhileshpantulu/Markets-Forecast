# BUILD_PROMPT.md: Hotel Market Stochastic Forecasting Application

You are building and then operating a forecasting application for a hotel investment team. The user uploads historical market performance and hotel pipeline data exported from CoStar. You do the rest: ingest, analyse history, research forward-looking drivers, simulate, backtest, write a sourced forecast memo, and publish results to a web frontend.

Read this whole file before doing anything. Then ask the questions in section 14 and wait for answers before writing code.

---

## 1. Objective

For a given hotel market (or submarket), produce a probability-weighted stochastic forecast of:

- Occupancy growth, year over year, as a percentage change, plus the change in occupancy points
- ADR growth, year over year, as a percentage change
- Derived RevPAR growth, year over year

The horizon is one stub year plus five full forecast years (configurable). The stub year is the current calendar year, built from year-to-date actuals plus simulated remaining months. The first full forecast year grows off the completed stub year.

The forecast must be:

1. Probability weighted, with a full distribution per year (mean, P10, P25, P50, P75, P90) and named scenarios with explicit probabilities.
2. Grounded in the uploaded CoStar history, with every forecast year placed in the context of the market's own historical performance.
3. Adjusted for forward-looking drivers: hotel supply pipeline, airport and air capacity, events gained or lost, macro and FX, tourism indicators, regulation, and geopolitical events including their second and third order effects.
4. Justified in writing, with every factual claim sourced and every assumption labelled as an assumption.
5. Backtested, with interval coverage reported honestly.

## 2. Operating rules

- Code computes the numbers. You write, run, test, and explain the code. Never type a forecast number that did not come from the simulation.
- Never invent data. If a series, source, or figure is unavailable, say so, record it as a gap, and use an explicitly labelled assumption instead.
- Never assume the current status of a geopolitical event, route, event, or policy from training data. Search for it at run time and date-stamp what you find.
- Every factual claim in any output carries a source: URL, publisher, publication date, and access date. Prefer primary sources (airport operators, statistics agencies, central banks, event organisers, company filings and press releases). Event status from news requires two independent sources.
- Keep facts and assumptions visibly separate in every output.
- Raw CoStar data is licensed. It never leaves the private repository. It is never deployed to a public web page. See section 10.
- Use a fixed random seed per run, recorded in the run metadata, so every run is reproducible.
- Writing style for all generated prose: direct sentences, no em dashes, no filler, no promotional language.
- When a methodological choice materially changes the result (for example, excluding 2020 to 2022 from parameter estimation), run both versions and report the difference.
- Commit in small steps with clear messages. Run the test suite before every commit.

## 3. Definitions and conventions

| Term | Definition |
| --- | --- |
| Stub year (Y0) | Current calendar year. YTD months are actual; remaining months are simulated. Reported as full-year growth versus the prior full year. |
| Y1 to Y5 | Full calendar years after the stub. Y1 grows off the simulated full-year Y0 level, path by path. |
| Occupancy growth % | Occ(t) / Occ(t-1) minus 1. Always also report the change in occupancy points. |
| ADR growth % | ADR(t) / ADR(t-1) minus 1, nominal, in the local currency of the export. Also report real ADR growth using the sourced CPI path. |
| RevPAR growth % | (1 + occ growth) x (1 + ADR growth) minus 1, computed path by path. |
| Supply, demand | Available and sold room nights per CoStar definitions. Occupancy = demand / supply. |
| Driver | A forward-looking factor converted into an explicit, sourced, quantified adjustment (section 6.6). |
| Scenario | A coherent combination of macro paths and driver settings with a probability (section 6.7). |

All of these are configurable in `config/markets/<market>.yaml`: horizon length, calendar or fiscal year, currency, geography level.

## 4. Inputs and the upload contract

The user uploads files through the GitHub web interface into:

```
inbox/<market-slug>/
  performance/   CoStar historical performance export(s): monthly preferred, annual accepted
  pipeline/      CoStar pipeline export(s): project, rooms, stage, projected opening date
  optional/      Anything else: airport statistics, events lists, operator data, notes.txt
```

Ingestion rules:

1. Inspect every file. Do not assume column names. Build or update `data/mappings/<source>.yaml` mapping source columns to the internal schema. If a mapping is ambiguous, stop and ask.
2. Validate: date continuity, duplicate months, supply x occupancy consistent with demand, ADR x demand consistent with revenue, unit and currency consistency, outliers. Write `runs/<market>/<run_id>/validation.md`. A failed hard check stops the run.
3. Store cleaned data as Parquet in `data/processed/<market>/`. Raw files stay in `inbox/` and are moved to `data/raw/<market>/<date>/` after processing. Both locations are private.
4. If the pipeline export has historical snapshots, keep them; they are used to calibrate delivery probabilities.
5. Read `optional/notes.txt` if present; it holds the user's context for the market.

Internal schema (minimum): `market, period_start, period_type, supply, demand, occupancy, adr, revpar, revenue, source_file`. Pipeline: `project_id, name, rooms, stage, projected_open, brand, chain_scale, source_file, snapshot_date`.

## 5. Historical analysis and context (runs first, every time)

Produce `history.md` and the data behind the frontend's history panel:

- Annual and monthly series for occupancy, ADR (nominal and real), RevPAR, supply, demand.
- Growth distributions: annual occupancy and ADR growth, with percentiles across the full history.
- Cycle map: identify peaks, troughs, drawdown depth, and recovery time for each downturn in the data. Label the known episodes in the record, such as the 2008 to 2009 financial crisis, the euro area crisis years, 2016, and 2020 to 2022, only where the data covers them.
- Supply and demand decomposition by year: how much of each occupancy change came from demand growth versus supply growth.
- Seasonality: monthly shares of annual demand and monthly ADR indices, and whether they have shifted.
- If a reference series is uploaded (national or regional), compare the market's growth and volatility to it.

Every forecast year must later be positioned against this history: its percentile in the historical distribution, the nearest historical analog year, and whether it implies occupancy or real ADR outside the historical range. Flag anything outside the historical P5 to P95.

## 6. Methodology

### 6.1 Overall structure

The simulation runs on monthly data for the stub year and annual data for Y1 to Y5. Each simulated path is a mixture draw:

1. Draw a scenario s with probability p(s).
2. Draw baseline uncertainty: demand growth, ADR residual, macro inputs.
3. Draw supply deliveries from the pipeline model.
4. Draw each driver's occurrence (Bernoulli with its probability, conditional on the scenario) and magnitude (from its distribution).
5. Combine through the occupancy identity and the ADR equation.

The probability-weighted forecast is the distribution across all paths. Per-scenario results are reported alongside it.

### 6.2 Stub year (Y0)

- Use actual YTD months.
- Simulate remaining months from: YTD year-over-year trend by month (weighted toward recent months), historical seasonality shares, and the same driver set restricted to remaining months.
- The stub's uncertainty band should be narrower than Y1 in proportion to the share of the year already observed. Test this.
- Report Y0 full-year growth versus the prior full year, and show actual versus simulated months separately in the frontend.

### 6.3 Baseline demand

- Model annual log demand growth with mean reversion: g(t) = mu + phi x (g(t-1) minus mu) + e(t). Residuals are bootstrapped from history or drawn from a Student-t fitted to history; choose by backtest.
- Add a shock regime: each year a market-wide shock occurs with probability p_shock, drawing a drawdown and recovery profile from the market's own history and any reference series. State the evidence for p_shock.
- Estimate parameters with and without 2020 to 2022 and report the sensitivity. Default to excluding those years from mu and phi while keeping them as shock evidence, unless the backtest favours otherwise.
- With a short history, shrink parameters toward a reference series if uploaded, or toward weakly informative priors written in `config/priors.yaml`. Report the shrinkage weight.
- The baseline represents what history implies. Drivers represent what is different about the future. This separation prevents double counting (section 6.6).

### 6.4 Supply and the pipeline

- Start from the latest observed supply.
- For each pipeline project, draw whether it opens within the horizon and when, using stage-based probabilities and delay distributions from `config/pipeline_priors.yaml`. If historical pipeline snapshots exist, calibrate those priors to observed conversion and delay. If not, mark the priors as assumptions for user review.
- Include an annual removal rate (closures, conversions to other uses) as an assumption with a range.
- Model ramp-up: new rooms add full supply on opening but reach a stabilised demand share over a configurable period.
- Include an induced-demand parameter with a range: the share of new-supply rooms that bring their own demand (new brands, new segments).
- Report expected supply growth per year with P10 and P90, and list the projects driving it.

### 6.5 Occupancy and ADR

- Occupancy(t) = Demand(t) / Supply(t), path by path, capped at a configurable ceiling informed by the market's monthly peak history.
- ADR log growth = CPI inflation(t) + beta x (log change in occupancy) + r(t) + ADR driver effects.
  - CPI path: research the latest published forecasts for the market's country from the central bank, the IMF, or the European Commission, cite them, and sample around them.
  - beta (pricing power): estimate from history, constrain to be non-negative, shrink toward a prior, report the estimate and its uncertainty.
  - r(t): AR(1) real ADR residual fitted to history.
- Demand shocks and ADR residuals are correlated, using the correlation estimated from history. A latent macro factor links macro-sensitive drivers so they move together within a path.

### 6.6 Forward-looking drivers

Drivers are researched by agents (section 7), written as cards in `runs/<market>/<run_id>/drivers.yaml`, reviewed by the critic, and approved by the user.

Driver card schema:

```yaml
- id: air-hub-diversion-2026
  title: Short descriptive title
  category: air_access | supply | events | macro | fx | geopolitical | policy | corporate | mice | leisure_sentiment | regulation | infrastructure | other
  affects: demand | adr | supply
  segment: leisure | corporate | group | transit | all
  mechanism_chain:
    - order: 1
      effect: Direct effect on travel flows
      sign: "+"
    - order: 2
      effect: Knock-on effect, for example on capacity or substitution
      sign: "+"
    - order: 3
      effect: Further effect, for example on prices, income, or policy
      sign: "-"
  timing: {start_year: 2027, peak_year: 2027, decay_years: 2}
  probability: 0.35
  magnitude: {dist: triangular, low: 0.2, mode: 0.8, high: 2.0, unit: pct_points_of_growth}
  conditional_on: [base, upside]
  already_in_baseline: none | partial | full
  baseline_overlap_note: Why this is or is not already captured by history
  counter_effects: [driver ids that offset this one]
  analogs:
    - event: Historical episode used to size the effect
      market: Where
      measured_effect: What was observed
      source: URL
  evidence:
    - claim: Specific factual claim
      url: https://...
      publisher: Name
      published: 2026-10-01
      accessed: 2026-11-06
  confidence: low | medium | high
  status: proposed | approved | rejected
  reviewer_note: ""
```

Driver research must cover, at minimum:

- **Supply:** confirm or challenge major pipeline projects through planning portals, developer and brand announcements, and press; identify projects missing from the export; closures and conversions; short-term rental regulation and enforcement that changes effective competing supply.
- **Air access:** airport passenger statistics and monthly trends from the airport operator; announced route launches, cuts, base openings or closures; terminal, runway, or slot changes; published traffic forecasts from bodies such as Eurocontrol or ACI. If airport history is uploaded, estimate the historical relationship between airport passengers and hotel demand and use it to size air-access drivers.
- **Events:** recurring trade fairs and congresses, including multi-year cycles that create odd-year and even-year swings; mega-events gained or lost; convention centre openings, closures, or expansions; event relocations to or from competing cities.
- **Macro and FX:** source-market GDP and consumer forecasts; exchange rates against the market's main source currencies; corporate travel indicators.
- **Tourism indicators:** national tourism board and statistics agency data, European Travel Commission and UN Tourism publications, forward booking or search indicators where publicly available, cruise schedules where relevant.
- **Policy and regulation:** tourist taxes, visa and entry rules, short-term rental rules, labour rules affecting hotel operations. Verify dates and status from primary sources.
- **Infrastructure:** rail links, metro lines, airport access, major urban regeneration.
- **Geopolitical:** see 6.6.1.

#### 6.6.1 Geopolitical and second-order analysis

For each live geopolitical situation or credible risk within the horizon, build an event map before writing driver cards:

1. Confirm current status at run date, with sources.
2. Trace transmission channels to this specific market:
   - Air routing and capacity: airspace closures, hub substitution, carrier capacity redeployment, longer routings and their cost.
   - Destination substitution: demand diverted away from competing destinations near the event, or away from this market if it is near.
   - Source-market effects: income, confidence, and FX in the countries that supply this market's visitors.
   - Energy: oil and jet fuel prices, fares, and fare elasticity by segment and haul.
   - Safety perception: travel advisories and their history of affecting arrivals.
   - Corporate and group: travel bans, MICE relocations, insurance.
   - Policy responses: interest rates, sanctions, visa changes.
3. For each channel, state the first, second, and third order effects on this market, with sign, lag, and duration.
4. Identify offsetting effects and net them explicitly. Positive and negative effects of the same event must appear together.
5. Find historical analogs with measured effects and use them to size magnitudes. Treat analogs as hypotheses to verify, cite the measurement, and explain how this case differs.
6. Assign probabilities to the event paths (escalation, persistence, resolution), not just the effects.

Worked illustration of the reasoning expected (verify every element at run time; do not treat as fact): a conflict that closes Middle East airspace or Gulf hubs could divert long-haul transfer traffic to European hubs, adding transit and stopover demand near those hubs (first order). Gulf carriers could redeploy capacity, and travellers could substitute away from the Eastern Mediterranean toward Western European destinations (second order). Higher oil prices could raise fares and squeeze source-market incomes, offsetting part of the gain, and a later reopening could release surplus capacity (third order). The net effect differs by market, segment, and year, and the driver cards must show that.

#### 6.6.2 Double counting

- The baseline embeds average historical trends. A driver only counts the part that differs from history. Example: steady airport growth in line with the last decade is already in the baseline; a new long-haul route or a hub closure is not.
- Every card states `already_in_baseline` and explains it. The critic rejects cards without this.

### 6.7 Scenarios and probability weighting

- Define scenarios in `runs/<market>/<run_id>/scenarios.yaml`: at least downside, base, and upside; add a severe downside when the shock evidence supports it.
- Each scenario specifies macro paths (GDP, CPI, FX), the shock regime setting, which drivers are active, and any magnitude scaling.
- Probabilities sum to 1. Justify each with evidence: published forecaster ranges, the history of shocks in this market, and the event-path probabilities from 6.6.1.
- Report per year: probability-weighted mean, P10, P25, P50, P75, P90 for occupancy growth %, occupancy points change, occupancy level, ADR growth %, ADR level, RevPAR growth %; per-scenario medians; probability that RevPAR growth is negative.
- Attribution: for each driver, its contribution to the mean and to the variance of each year's growth, by removal or Shapley decomposition. This feeds the "what drives the forecast" section.

### 6.8 Simulation

- Default 20,000 paths, configurable. Vectorise with NumPy. Seed recorded.
- Export a down-sampled set of baseline paths (default 2,000) plus driver definitions so the frontend can recompute what-if cases (section 9).

### 6.9 Backtesting and calibration

- Rolling-origin backtest of the baseline engine: for each origin year with enough prior history, forecast Y1 to Y5 using only data available at that origin.
- Report: P10 to P90 interval coverage by horizon (target about 80%), CRPS, mean bias, and comparison against two naive benchmarks (zero growth and historical mean growth).
- If coverage is materially off target, apply a documented calibration factor to the spread and disclose it.
- State plainly that drivers cannot be backtested point in time, because forward-looking evidence from past dates is not available.

### 6.10 Plausibility checks (must pass or be explained)

- P50 growth outside the historical P5 to P95 for that measure.
- Occupancy above the historical annual maximum.
- Real ADR growth above 3% for more than two consecutive years at P50.
- Supply absorption: occupancy holding flat while supply grows faster than the historical maximum.
- Any driver magnitude larger than its best analog without explanation.
- Scenario probabilities without cited justification.

## 7. Agent architecture

Create subagents in `.claude/agents/`, each with a narrow role, its own instructions, and the tools it needs:

| Agent | Job | Output |
| --- | --- | --- |
| ingest | Maps and validates uploads | `validation.md`, processed data |
| historian | Historical analysis and cycle map | `history.md`, history data |
| supply-analyst | Pipeline verification, missing projects, removals | supply driver cards, pipeline notes |
| demand-researcher | Air access, events, tourism, macro, policy, infrastructure | driver cards |
| geopolitics-analyst | Event maps and second and third order chains | event maps, driver cards |
| critic | Applies 6.6.2 and 6.10, checks sources, challenges magnitudes and probabilities | `critique.md`, card status changes with reasons |
| reporter | Writes the memo and builds outputs | memo, Excel, results.json |

Orchestrate with a project skill or command, `/forecast <market-slug>`, that runs: ingest, historian, supply-analyst and demand-researcher and geopolitics-analyst in parallel, critic, simulation, backtest, plausibility checks, reporter. The critic's rejections loop back to the relevant agent once; anything still unresolved is listed for the user.

Write a `CLAUDE.md` at the repo root that records these rules, the schema, the sourcing standard, the writing style, and the data-licensing rule, so every future session follows them.

## 8. Outputs per run

Folder: `runs/<market>/<run_id>/` where run_id is `YYYY-MM-DD-<short-hash>`.

1. `results.json`: all distributions, scenario results, attribution, history context, backtest metrics, metadata (seed, data files, code version, driver statuses). Define and validate it with a Pydantic or JSON Schema model.
2. `drivers.yaml`, `scenarios.yaml`, `critique.md`, `validation.md`, `history.md`.
3. `memo.md`: the forecast memo, structured as:
   - Headline table: Y0 (stub) and Y1 to Y5 occupancy growth %, occupancy points change, ADR growth %, RevPAR growth %, each with probability-weighted mean, P10, P50, P90.
   - What drives the forecast: top drivers by contribution, with their mechanism chains.
   - Historical context: where each year sits in the market's history, nearest analog years, comparison with past cycles.
   - Scenarios: definitions, probabilities, justifications, per-scenario results.
   - Supply outlook: expected deliveries and key projects.
   - Geopolitical and macro event maps.
   - Risks and what would change the view.
   - Method and backtest results, including coverage and limitations.
   - Gaps and assumptions, listed separately from facts.
   - Sources, numbered, with access dates.
   - Status line: provisional until all drivers are approved.
4. `forecast.xlsx`: one tab only, kept simple. Rows are measures and statistics; columns are Y0 to Y5. Blocks: probability-weighted mean, P10, P50, P90, then one row per scenario median. No formulas beyond simple references, no extra tabs.
5. `report.html`: a single self-contained HTML file (all JS, CSS, and data inlined) showing the same views as the web app for this run. It opens offline in a browser and can be attached to an email.

## 9. Web frontend

Build a static web app in `web/` that reads `runs/index.json` and each run's `results.json`. Use Vite with TypeScript and a single charting library (ECharts or Plotly), with exact pinned versions. Also build the single-file `report.html` per run from the same code.

Views:

1. **Market and run selector.** Latest run by default; earlier runs available for comparison.
2. **Forecast.** One continuous timeline per measure from the first historical year through Y5: history as a solid line, the stub year split into actual and simulated months, forecast as a fan (P10 to P90, P25 to P75, P50 line, mean marker). Toggle between growth % and levels.
3. **Scenarios.** Table of scenarios with probabilities and per-year medians, and a chart of scenario medians against the weighted distribution.
4. **Drivers.** Register of driver cards with filters by category, status, and year; each card shows its chain, magnitude, probability, analogs, sources, and critic notes.
5. **What-if.** Toggle drivers on or off, change their probabilities, and change scenario weights; the app recomputes the weighted distribution in the browser from the exported baseline paths. Show clearly that what-if results are exploratory and differ from the official run.
6. **History.** Cycle map, growth distributions, supply and demand decomposition, seasonality.
7. **Method.** Backtest coverage by horizon, benchmark comparison, plausibility checks, assumptions, gaps.
8. **Downloads.** Memo, Excel, report.html.

Requirements:

- The browser what-if engine must reproduce the Python engine exactly for the same inputs. Write a parity test that runs both on fixtures and fails on any difference beyond floating-point tolerance.
- Audience: hotel investment analysts and principals reviewing a market view before an investment committee. Design for dense, accurate reading: tabular figures, clear units in every axis and header, percentages to one decimal place, occupancy points to one decimal place. Light and dark themes. Responsive. Keyboard accessible. Avoid templated dashboard styling: no identical card grids, no decorative gradients, no all-caps labels.
- Every number shown links back to its source in `results.json`; every factual statement links to its source URL.

## 10. Hosting and data protection

- The repository must be private.
- GitHub Pages sites are publicly accessible unless the organization uses GitHub Enterprise Cloud and the site is published privately. Before any Pages deployment, check the site's visibility through the REST API (`gh api repos/{owner}/{repo}/pages`). Deploy only if it is private. If you cannot confirm that, do not enable Pages; the user uses `report.html` from the run folder instead.
- Never commit raw CoStar files outside `inbox/` and `data/raw/`, and never include raw rows in any web build. The web build contains only derived forecast and summary data, and only deploys privately.
- Add a CI check that fails if any file under `web/dist/` contains raw-data fields, or if a deploy workflow runs while Pages visibility is public.

## 11. Automation

Write `ops/ROUTINE_PROMPT.md`, the prompt for a Claude Code routine with a GitHub push trigger on this repository. The routine:

1. Exits immediately unless the triggering push changed files under `inbox/` or `runs/*/*/drivers.yaml` or `runs/*/*/scenarios.yaml`.
2. Exits immediately if the push was made by a Claude session, for example if the head commit carries a `Claude-Session` trailer, to prevent loops.
3. On new files in `inbox/<market>/`: runs `/forecast <market>`.
4. On edited driver or scenario files: re-runs simulation, checks, and outputs only, keeping the research.
5. Pushes results to a branch named `claude/forecast-<market>-<run_id>` and opens a pull request whose description contains the headline table, the top drivers, the critic's open issues, and the list of drivers awaiting approval.

The user approves drivers by editing `status` in `drivers.yaml` on the PR branch, which triggers a re-run. Merging the PR updates `runs/index.json` and, where section 10 allows, deploys the web app through a GitHub Actions workflow.

Also document how to run the same pipeline manually from a Claude Code session: "Run /forecast <market-slug>".

## 12. Repository structure

```
.
├── CLAUDE.md
├── BUILD_PROMPT.md
├── README.md                     Plain-English guide: how to upload, review, approve, read outputs
├── .claude/
│   ├── agents/                   Subagent definitions (section 7)
│   └── commands/ or skills/      /forecast orchestration
├── config/
│   ├── markets/<market>.yaml
│   ├── priors.yaml
│   └── pipeline_priors.yaml
├── inbox/<market>/{performance,pipeline,optional}/
├── data/{raw,processed,mappings}/
├── engine/                       Python package
│   ├── ingest/  history/  supply/  demand/  adr/
│   ├── drivers/  scenarios/  simulate/  backtest/  checks/
│   └── outputs/                  results.json, memo, Excel, report data
├── runs/<market>/<run_id>/
├── web/
├── ops/ROUTINE_PROMPT.md
├── tests/
├── requirements.txt              Pinned
└── .github/workflows/            CI tests, data-leak check, optional private Pages deploy
```

Python dependencies, pinned: pandas, numpy, scipy, statsmodels, pydantic, pyyaml, pyarrow, openpyxl, jinja2, pytest. Add others only with a stated reason.

## 13. Build plan

Work in phases. At the end of each phase: run all tests, update README and CLAUDE.md, commit, and give me a short summary of what was built, what was tested, and any decision that needs my input. Stop at the marked checkpoints.

- **Phase A: scaffold and ingestion.** Repo structure, CLAUDE.md, config, schema, ingestion and validation. Generate a synthetic CoStar-like dataset for tests only, stored under `tests/fixtures/`, never mixed with real data. Then process my real sample upload. **Checkpoint: show me the mapping and validation report.**
- **Phase B: history, stub year, baseline, backtest.** Historical analysis, stub-year engine, baseline demand and ADR models, rolling-origin backtest. **Checkpoint: show me backtest coverage and benchmark comparison before adding drivers.**
- **Phase C: supply.** Pipeline model, removals, ramp-up, induced demand.
- **Phase D: drivers.** Subagents, driver card schema and validation, event maps, critic.
- **Phase E: scenarios and simulation.** Mixture simulation, attribution, plausibility checks. **Checkpoint: a full provisional run for the pilot market, with the memo.**
- **Phase F: outputs.** results.json schema, memo template, one-tab Excel, report.html.
- **Phase G: frontend.** All views, what-if engine, parity test.
- **Phase H: automation and hosting.** Routine prompt, CI workflows, data-leak check, Pages visibility check, README for a non-developer.

Testing standards: unit tests for every model component; property tests (for example, occupancy never exceeds the ceiling, probabilities sum to 1, stub bands narrower than Y1 when YTD months exceed zero); a regression test that a fixed seed and fixed inputs reproduce identical results; the parity test; schema validation of every output.

## 14. Questions to ask me before writing code

Ask these, wait for answers, and record the answers in CLAUDE.md:

1. Which market or submarket is the pilot, and have you uploaded a sample CoStar performance export and pipeline export to `inbox/`?
2. Is the performance export monthly or annual, and at which geography level?
3. Does the pipeline export include project stage, projected opening date, and historical snapshots?
4. Is a national or regional reference series available to upload?
5. Is this repository owned by an organization on GitHub Enterprise Cloud with private Pages publishing enabled?
6. Calendar year or fiscal year, and if fiscal, which months?
7. Which currency should ADR be reported in?
8. Do you have airport passenger history or an events list for the pilot market to upload?
9. Are there markets, sources, or drivers you want excluded?
