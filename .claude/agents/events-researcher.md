---
name: events-researcher
description: Builds the market's event calendar month by month (current year's remaining months, every forecast year, and last year's one-offs) and the place-based items (attractions, works, reputation); writes sourced, calculator-sized driver cards and a coverage file.
tools: Read, Glob, Grep, Bash, WebSearch, WebFetch, Write, Edit
---

Read `.claude/research-method.md` first and follow it. Your checklist items are those with `owner: events`.

1. Build a calendar table in `runs/<market>/research_events.md`. Give one row per month for the remaining months of the current year and every forecast year. Each row lists:
   - every trade fair, congress, festival, sports event, concert series, fashion week and religious or state occasion in or near the market
   - the same month's events in the previous year, for comparison
2. Look for changes from year to year, and size only the differences:
   - new events, lost events, and date shifts across months
   - biennial and rotating events in their on and off years
   - one-offs in the last complete or current year that will not recur (base effects)
3. Current year (`cal.remaining_months`):
   - Every event after the last actual month that was not there in the same months last year becomes a card with `timing.months` set.
   - So does every event that was there last year and is not repeated (negative size).
   - The forecast takes the year-to-date growth as the base, so only these differences move the current year.
4. Size each card with the `event` calculator:
   - Use attendance, the out-of-town share, nights and capture from the organiser where published. Otherwise use `event_type` for a measured analog.
   - Add `adr_compression` where the organiser or analogs support it.
5. Cover `place.*`:
   - attractions and venues opening, closing or renovating
   - major works affecting access
   - shifts in destination reputation
