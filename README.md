# Hotel Market Forecast

This tool forecasts occupancy, ADR and RevPAR growth for a hotel market. It covers the current year plus five full years, with probability ranges and named scenarios, and it works from CoStar exports.

The build is in progress. What works today:

- loading and checking the data
- finding anomalies in the history, such as series breaks
- a **provisional baseline forecast**: occupancy, ADR and RevPAR growth for the current year and five more, with P10 to P90 ranges. It is built only from the market's own history and hotel pipeline.
- a backtest showing how often past forecasts' ranges contained what actually happened
- a drag-and-drop web page that shows all of the above

Forward-looking drivers (air access, events, macro, geopolitics), scenarios, the memo and the Excel output come in later phases. Until then, read the forecast as "what history and the pipeline alone imply".

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
