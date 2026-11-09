# Research method (shared by the forecast researchers)

How every researcher works (events-researcher, demand-researcher, supply-analyst, geopolitics-analyst). Follow CLAUDE.md.

**Scope.** Your items are the entries in `config/research_checklist.yaml` with your `owner`. Every one must be answered in
`runs/<market>/coverage_<owner>.yaml`:

```yaml
coverage:
  - item: cal.recurring
    status: card              # card | no_material_effect | not_applicable | could_not_verify
    cards: [maison-objet-2027]
    finding: One sentence on what you found and why it matters or not.
    sources: [{publisher: ..., url: ..., published: ..., accessed: ...}]
    searches: ["maison et objet dates 2027", "salon paris janvier 2027 dates"]
```

A nil finding (`no_material_effect`) is a result, not a gap. It still needs the sources you checked and the searches you ran.
`could_not_verify` says what you tried and what blocked you.

**Searching.**
- At least three searches per item, including one in the local language. Use French for Paris, German for Berlin, Italian for Rome, Spanish for Madrid, and so on.
- Prefer primary sources: organisers, convention bureaus, airports, statistics offices, city and national governments, central banks, company filings.
- Event status from news needs two independent sources.
- Search at run time. Never take a current status from memory.
- Date-stamp everything with published and accessed dates.

**Time frame.** Cover:
- the remaining months of the current year against the same months last year
- every year of the horizon
- the last complete year, for one-offs that will not recur

**Orders of effect.** For each finding, write the chain as `mechanism_chain`:
- first order: the direct demand or rate change
- second order: how the market reacts (displacement, pricing, airline or supply response)
- third order: slower knock-ons (repeat visits, reputation, new supply)

Offsetting effects go on the same card or as linked cards.

**Sizing.**
- Use a calculator from `engine/ledger/calculators.py` whenever the inputs can be stated (event, air_capacity, source_market, price_change, competing_supply).
- Inputs may be [low, most likely, high].
- Every input is either sourced or labelled as an assumption in `magnitude_note`.
- For event analogs, `config/event_analogs.yaml` lists measured past events.
- State `already_in_baseline` and explain the overlap. The baseline is the long-run CAGR, so only what differs from the market's normal year counts.

**Writing.**
- Cards go to `runs/<market>/drivers_<owner>.yaml` with status `proposed`.
- Notes go to `runs/<market>/research_<owner>.md`.
- Direct sentences, no em dashes.
- CoStar figures from results.json may guide you but never appear in evidence.
