---
description: Run the full forecast pipeline for one market (ingest, history, research agents, critic, simulation, backtest, outputs)
argument-hint: <market-slug>
---

Run the forecast pipeline for market `$ARGUMENTS`. Follow CLAUDE.md throughout.

1. **Ingest.** Use the `ingest` agent. Stop and ask the user if validation fails or a mapping is ambiguous.
2. **Baseline.** Run `python -m engine $ARGUMENTS` (add `--inbox` if the files are not in `inbox/$ARGUMENTS/`). This writes history.md, backtest.md, baseline.md and results.json.
3. **History.** Use the `historian` agent.
4. **Research, in parallel.** Run the `supply-analyst`, `demand-researcher` and `geopolitics-analyst` agents. Each appends proposed cards to `runs/$ARGUMENTS/drivers.yaml`.
5. **Critic.** Use the `critic` agent. It approves or rejects each card with a reason. Rejected cards go back to their author once. Anything unresolved is listed for the user.
6. **Forecast with drivers.** Run `python -m engine $ARGUMENTS --drivers runs/$ARGUMENTS/drivers.yaml`. Check `explanation.flags`. If a flag is not explained in the critique, explain it or fix the cause.
7. **Report.** Use the `reporter` agent to write memo.md.
8. **Summarise for the user:**
   - the headline table
   - the top drivers by contribution
   - open critic issues
   - drivers awaiting approval
   - any gaps

   Remind the user that drivers and settings can be adjusted on the web page (Assumptions tab) or by editing drivers.yaml.

Do not commit anything under runs/, inbox/ or data/ while config/hosting.yaml says the repository is public.
