# Hotel Market Forecast

This tool forecasts occupancy, ADR and RevPAR growth for a hotel market. It covers the current year plus five full years. Each forecast comes with probability ranges and named scenarios, and it is built from CoStar exports.

The build is in progress. Phase A covers loading and checking data, and it works now. The forecasting engine, the research agents and the web page are added in later phases.

## Important: the repository is public

At the moment, CoStar data must not be uploaded to this repository. CoStar data is licensed, and anyone can read a public repository. To make the repository private:

1. Open the repository on GitHub.
2. Go to Settings, then General, then Danger Zone.
3. Choose Change visibility, then Private.

Then tell Claude, which will update `config/hosting.yaml` and remove the upload block.

The web page that is still to be built does not need the data to be in the repository. You drop files onto the page and they are processed inside your browser. Nothing is uploaded.

## How to upload data (once the repository is private)

Put files in `inbox/<market-slug>/` using the GitHub web interface (Add file, then Upload files):

| Folder | What goes there |
| --- | --- |
| `performance/` | The CoStar DataTable export (monthly performance) |
| `pipeline/` | The CoStar property export of pipeline hotels (under construction, renovation, proposed) |
| `reference/` | A country or regional DataTable export in the same format, used for comparison |
| `optional/` | Airport statistics, an events list, and `notes.txt` with anything Claude should know about the market |

Keep the file names that CoStar gives them, because the export date in the name is used as the snapshot date.

## How to check the data

Run `python -m engine.ingest paris-louvre-marais`. It writes `runs/<market>/<run_id>/validation.md`, which contains:

- which source column fed which field
- every check, with its result (pass, warning or FAIL)
- an annual summary
- the pipeline as it was read
- the assumptions made

A FAIL stops the run, and the uploaded files stay in `inbox/` so you can fix them and try again.

## Markets configured

- `paris-louvre-marais`: Paris, Louvre/Marais submarket, EUR, calendar year.

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

See `CLAUDE.md` for the working rules and `BUILD_PROMPT.md` for the full specification.
