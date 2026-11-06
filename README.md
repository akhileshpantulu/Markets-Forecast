# Hotel Market Forecast

This tool forecasts occupancy, ADR and RevPAR growth for a hotel market. It covers the current year plus five full years, with probability ranges and named scenarios, and it works from CoStar exports.

The build is in progress. What works today:

- loading and checking the data
- finding anomalies in the history, such as series breaks
- a **provisional baseline forecast**: occupancy, ADR and RevPAR growth for the current year and five more, with P10 to P90 ranges. It is built only from the market's own history and hotel pipeline.
- a backtest showing how often past forecasts' ranges contained what actually happened
- a drag-and-drop web page that shows all of the above

- **drivers**: events and risks, each with a probability and a size. They can come from a drivers file or be added on the page.
- an **Assumptions tab** where you can change any weight or bias and rerun in a few seconds

Scenarios, the memo and the Excel output come in later phases.

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

After a run, open **Assumptions**. You can change:

- **Pipeline:** the chance each stage opens within the forecast. The defaults are under construction 90%, final planning 60%, planning 50%, proposed 40%. You can also change the most likely delay.
- **Supply:** the share of rooms closed each year, how much demand new and reopened hotels bring, and hotels not yet in the export.
- **Demand:** a tilt to the trend, in points of growth a year, and multipliers on the chance of a moderate or severe shock.
- **ADR:** a tilt to the real drift, and pricing power.
- **Inflation path, band width, and the occupancy ceiling.**
- **Drivers:** switch each one on or off, or change its chance of happening. You can add your own event or risk:
  - Demand or ADR, as a percent of that year's level.
  - A shock chance multiplier, for unrest or other risks.
  - Extra rooms.
  - Each can be one-off, temporary or permanent.
  - Event chances follow a ladder unless you set them: rumour 17.5%, credible report 40%, announced 80%, tickets on sale 90%, held 100%.

Press **Rerun forecast**. Your changes are listed at the top of the forecast and in the reports as user adjustments, so they stay separate from the model's estimates. Settings are remembered in your browser for each market. Use **Download settings** and **Download drivers** to keep or share them.

## The drag-and-drop page

You drop your CoStar files onto the page, and the Python engine runs inside your browser. **Nothing is uploaded.** The page itself contains code only and no data, so it can be hosted publicly without exposing licensed CoStar data.

Upload slots:

| Slot | File | Status |
| --- | --- | --- |
| Market performance | CoStar DataTable export (monthly) | Working |
| Pipeline | CoStar property export of pipeline hotels | Working |
| Airline and airport data | To be defined from your example | Coming next |
| Country performance | Country-level DataTable export | Coming next |

After you click Run, the page shows:

- the baseline forecast: headline table, fan charts against history, the current year split into actual and simulated months, and where each year sits in the market's own history
- history: growth ranges, past downturns and recoveries, supply versus demand, seasonality
- the backtest: coverage by years ahead, and a comparison with simple benchmarks
- supply: expected supply growth and each project's chance of opening by year
- annual and monthly history charts, plus the annual table
- every validation check
- the guardrails: series breaks, which years the model may fit on, and the influence check
- the pipeline
- the column mapping
- the full report, which you can download

### Option 1: GitHub Pages (free, from this repository)

1. Merge the working branch into `main`.
2. On GitHub, open the repository's **Settings > Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Pushes to `main` that touch the web page or the engine now publish the site automatically through `.github/workflows/pages.yml`. You can also start it by hand from **Actions > deploy-web > Run workflow**.
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
