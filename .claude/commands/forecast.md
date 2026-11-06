---
description: Research forward-looking drivers for one market and produce a drivers document (drivers.yaml plus drivers.md) to drop into the forecast page
argument-hint: <market-slug>
---

Produce the drivers document for market `$ARGUMENTS`. Follow CLAUDE.md throughout. The forecast itself runs on the web page; this command only researches, checks and documents drivers.

**Output:** `runs/$ARGUMENTS/drivers.yaml` (the file the user drops into the Drivers slot on the page) and `runs/$ARGUMENTS/drivers.md` (the readable document: every card's reasoning, chance, size, sources and checks).

## 0. Market and inputs

- If `config/markets/$ARGUMENTS.yaml` does not exist, create it (name, CoStar market and submarket, currency, country ISO2, horizon 5). Ask the user only if the currency or country is unclear. Market configs hold no data and may be committed.
- Refresh near-term inflation: search for the latest official forecasts (UK: OBR calendar-year CPI and the Bank of England Monetary Policy Report; France: Banque de France projections, HICP) and the latest actual print. Update `config/macro/near_term/<ISO2>.yaml` with value, measure, publisher, title, URL, published and accessed dates, and how each figure was verified. These are public statistics and may be committed.
- Nothing has to be uploaded. Two inputs are optional and make the cards better:
  - **CoStar performance and pipeline exports** (the same files the user drops on the page), in `inbox/$ARGUMENTS/` or a path the user gives. If present, run `python -m engine $ARGUMENTS --inbox <path>` once. Its `results.json` gives the researchers the market's long-run trend, shock history and pipeline projects, and lets the critic check for double counting.
  - **results.json downloaded from the page** (Reports tab, "Download results.json"). It serves the same purpose when the raw exports are not available here.
- Without either, say so at the top of the summary: cards are then sized against analogs and priors only, and the supply analyst cannot check the CoStar pipeline project by project.
- Never commit anything under `inbox/`, `data/` or `runs/` while `config/hosting.yaml` says the repository is public.

## 1. Research, in parallel

Run the `supply-analyst`, `demand-researcher` and `geopolitics-analyst` agents at the same time. Pass each the market slug and the path to `results.json` if there is one. Each writes its own file with status `proposed`:

- `runs/$ARGUMENTS/drivers_supply.yaml`
- `runs/$ARGUMENTS/drivers_demand.yaml`
- `runs/$ARGUMENTS/drivers_geopolitics.yaml` (plus `event_maps.md`)

## 2. Critic

Run the `critic` agent on the three files. It approves or rejects each card with a `reviewer_note`, edits the card files in place, and writes `runs/$ARGUMENTS/critique.md`. A rejected card goes back to its author once. Anything still unresolved stays `proposed` and is listed for the user.

## 3. Build the document

Run `python -m engine.drivers.doc $ARGUMENTS`. It merges `runs/$ARGUMENTS/drivers_*.yaml`, runs the automated checks and writes `drivers.yaml` and `drivers.md`. Fix any card with an error and run it again until there are no errors.

## 4. Summarise for the user

- where the two files are, and that `drivers.yaml` goes into the Drivers slot on the page
- a short table: driver, affects, year, chance, size, status
- cards the critic rejected or left open, and why
- whether the cards were checked against the market's own baseline (step 0)
- that each driver can be switched off or given a different chance on the page's Assumptions tab, and that proposed cards count only while "Include proposed drivers" is on
