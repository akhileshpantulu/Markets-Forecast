# Hotel Market Forecast

This tool forecasts occupancy, ADR and RevPAR growth for a hotel market, for the current year plus five full years, from CoStar exports.

Each year's forecast is a **ledger**: the market's long-run RevPAR CAGR, then named adjustments, each with its reason and its basis.

1. **Baseline.** The long-run RevPAR CAGR. Occupancy is held flat and rate carries the growth (you can split it by each CAGR instead). Rate also moves by inflation against its historical average.
2. **Supply.**
   - Hotel stock growing above or below its long-run trend, sized by how RevPAR responded across 298 European submarkets: occupancy -0.51 per point the same year, ADR -0.29 the next.
   - Temporarily closed hotels reopening.
   - The tier and product type of the hotels opening and closing, which move the market's average rate and occupancy.
   - How directly the new rooms compete with the market's core.
3. **Drivers.**
   - Events, air access, source markets and exchange rates, taxes and policy, competing supply, and geopolitical risk.
   - Each is sized by a calculator that shows its arithmetic, and weighted by its chance.
   - Effects are sized by month and added up to the year.

RevPAR growth equals occupancy growth combined with ADR growth. The P10 to P90 range comes from how far the long-run CAGR rule has missed historically, scaled to the market's own volatility, plus the uncertainty of the pipeline and the drivers.

Every coefficient is in `config/calibration.yaml` with its status: fitted on data, a labelled assumption, or your decision. You can change it on the page. Fitted values are refreshed from the private European panel with `python -m engine.ledger.calibrate`.

## Running the research in Cowork

1. Run the market on the page, open **Assumptions**, and click **Download research brief**. The brief is written for that market: the months still to cover this year, the 45-item checklist, the method, the pipeline projects to verify, the card format and the calculators.
2. In Claude Cowork, attach the brief and ask it to follow it. It researches with your browser and saves one file, `drivers-<market>.yaml`.
3. Drop that file into the **Drivers** slot and run again. The page lists any card it cannot use, shows the research coverage (every checklist item answered or not), and puts each driver into the year-by-year ledger.

You can also install `cowork/hotel-market-research/SKILL.md` as a skill. It is the same brief without the market details, so Cowork asks for them.

## Getting a drivers document

Drivers come from research agents that run in a Claude Code session, not on the page. In a session on this repository, run:

```
/forecast <market-slug>
```

It researches supply, demand and geopolitical drivers, has a critic approve or reject each one, and writes two files to `runs/<market-slug>/`:

- `drivers.yaml`: drop it into the **Drivers** slot on the page.
- `drivers.md`: the readable version. For every driver it gives how it works, its chance and size (labelled as judgements), what is already in the historical baseline, the sources with dates, the critic's note and the automated checks.

You do not need to upload anything for this. Two optional inputs make the cards better:

- **Your CoStar performance and pipeline exports.** The session runs the baseline from them, so the agents can see the market's long-run trend, its past shocks and the pipeline project by project. The critic uses this to reject drivers that are already in the trend (double counting) and to size each driver against the market's own growth.
- **The `results.json` from the page** (Reports tab). It gives the same information when you would rather not share the raw exports.

The validation report is never needed. Both inputs hold licensed CoStar figures, so they stay in the session and are never committed while this repository is public.

To build the document from card files you wrote or edited yourself, run `python -m engine.drivers.doc <market-slug> [files...]`.

## Adjusting assumptions (Assumptions tab)

The tab lists every coefficient with its basis and evidence, the pipeline probabilities by stage, the supply floor, the inflation path, and the drivers. Change any of them and press **Rerun forecast** (under a second). Your changes are listed on the forecast as your adjustments.

## The drag-and-drop page

You drop your CoStar files onto the page, and the Python engine runs inside your browser. **Nothing is uploaded.** The page itself contains code only and no data, so it can be hosted publicly without exposing licensed CoStar data.

Upload slots: **market performance** (CoStar DataTable export, monthly), **pipeline** (CoStar property export), and optionally **drivers** (the drivers.yaml from `/forecast`).

After you click Run, the page has four tabs:

- **Forecast:** headline table with P10 to P90 ranges, a chart against history, and each year's ledger with its reasons; downloads of forecast.md, ledger.csv and results.json.
- **Supply:** inventory against its trend, openings, reopenings and closures by year, and each pipeline project's tier, product type and chance of opening.
- **Map:** the pipeline projects numbered by opening date and pinned on a map, coloured by stage, with supply by year. **Find locations** looks the addresses up on OpenStreetMap (the only time anything leaves your browser); results are remembered. **Export pipeline (Excel)** writes the pipeline in your template: the map, the project table, supply by year with the CAGR, and the raw CoStar columns. The same export button is on the Supply tab.
- **Assumptions:** every coefficient with its basis and evidence, the pipeline probabilities, the inflation path and the drivers. Change any and rerun.
- **Data:** validation checks that need attention, the files read, series breaks, and validation.md.

### Option 1: GitHub Pages (free, from this repository)

1. On GitHub, open the repository's **Settings > Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
2. Every push to the working branch or `main` that touches the page, the engine or the config publishes the site automatically through `.github/workflows/pages.yml`, as does each finished public-data refresh.
4. The page will be at `https://akhileshpantulu.github.io/Markets-Forecast/`.

### Option 2: Vercel

1. Sign in at vercel.com with GitHub and choose **Add New > Project**, then import this repository.
2. Set **Root Directory** to `web`. The build settings come from `web/vercel.json`, so leave the rest as they are.
3. Deploy. Every push redeploys. Pushes to branches other than `main` get their own preview URLs.

### Option 3: a single file on your computer

The hosted site also serves `forecast-app.html`, the whole app in one file. Download it from `<your site>/forecast-app.html` and open it in Chrome or Edge.

- It needs an internet connection the first time it loads the Python runtime from the jsDelivr CDN.
- Your files are still processed only on your machine.

To build it yourself, run `cd web && npm ci && npm run build:all`. The file appears at `web/dist/forecast-app.html`.

## The repository is public

Do not commit CoStar files here. The drag-and-drop page does not need them in the repository. If you want the automated GitHub pipeline later (upload to `inbox/` and get a forecast pull request), make the repository private first: **Settings > General > Danger Zone > Change visibility**.

## Data checks and guardrails

Each run writes a validation report covering:

- **Column mapping:** which source column feeds which field.
- **Identities:** occupancy = demand ÷ supply, ADR = revenue ÷ demand, and RevPAR = occupancy × ADR.
- **Continuity:** no missing or duplicate months.
- **Reconciliation:** the monthly values match CoStar's own 12-month and year-over-year columns.
- **Closures:** temporary closures, reconciled with the pipeline.
- **Outliers.**

The guardrails decide what history the model may learn from:

- **Series breaks.** A step that hits all twelve months of a year in the same direction and does not reverse usually means the CoStar sample or definitions changed. One example is ADR in 1999 for Paris, Louvre/Marais. Growth that contains a break is left out of model fitting, and earlier levels are rescaled so ranges compare like with like. A step too recent to confirm is flagged for review, not removed.
- **COVID years** (2020 to 2022) are left out by default. The model is also run with them included, as a sensitivity check.
- **Influence check.** Each year is left out in turn. Any single year that moves the fitted average far more than a typical year does is flagged, and the model is fitted with and without it.

To keep a detected break in the data, add it under `estimation.series_breaks` in the market file in `config/markets/` with `action: keep`.

## For developers

```bash
python -m venv .venv
```

```bash
. .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

```bash
python -m pytest
```

```bash
cd web && npm ci && npm run build:all
```

See `CLAUDE.md` for the working rules and `BUILD_PROMPT.md` for the full specification.
