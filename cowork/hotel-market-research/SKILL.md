---
name: hotel-market-research
description: Research forward-looking drivers for a hotel market forecast (events, air access, source markets, policy, supply, risk) against a 45-item checklist, and save one drivers YAML file to drop into the forecast page.
---

# Driver research brief: <market name>

You are researching what will make this hotel market's occupancy and rate differ from its long-run trend over the forecast years. The forecast page already holds the market's history, its long-run growth and its hotel pipeline. Your job is the drivers: sourced, sized and weighted by their chance. Leave no stone unturned: every checklist item below must be answered, either with driver cards or with a recorded finding that nothing material applies.

## Before you start

Ask for, or read from the attached brief: the market and submarket, the country, the currency, the last month of actual data, and the forecast years.

## How to research

- At least three searches per checklist item, including one in the local language (French for Paris, German for Berlin, Italian for Rome, Spanish for Madrid, and so on).
- Open the pages, not just the search results. Prefer primary sources: organisers, convention bureaus, airports and airlines, statistics offices, city and national governments, central banks, company filings.
- Event status taken from news needs two independent sources. Never rely on memory for a current status; search now and record published and accessed dates.
- For every finding, write the chain of effects: first order (the direct demand or rate change), second order (the market's reaction: displacement, pricing, airline or supply response), third order (slower knock-ons). Offsetting effects go on the same card or a linked card.
- The baseline is the market's long-run growth, which already contains its normal calendar and its usual share of shocks. Only differences from a normal year count. A fair that recurs every year unchanged is not a driver; a fair that moves month, skips a year or doubles in size is.
- Do not use, copy or quote CoStar forecasts or CoStar data in evidence.

## Divide the work

Split the research into the four streams below and run them at the same time, one sub-agent per stream, if you can start sub-agents or parallel tasks. If you cannot, work through the streams one after another in this order. Either way, finish with the review pass.

1. **Events and place** (card ids start `events-`): `cal.remaining_months`, `cal.recurring`, `cal.rotations`, `cal.mega_future`, `cal.concerts`, `cal.base_effects`, `cal.congress_pipeline`, `place.attractions`, `place.works`, `place.image`.
2. **Access, source markets, corporate and policy** (card ids start `demand-`): `air.traffic`, `air.routes`, `air.capacity`, `rail.links`, `cruise`, `src.mix`, `src.macro`, `src.fx`, `src.costs`, `src.longhaul`, `corp.employers`, `corp.travel`, `corp.academic`, `pol.taxes`, `pol.entry`, `pol.str`, `pol.overtourism`, `pol.labour`.
3. **Supply** (card ids start `supply-`): `sup.pipeline_check`, `sup.missing`, `sup.closures`, `sup.reopenings`, `sup.repositioning`, `sup.alternative`, `sup.policy`, `sup.tier_check`.
4. **Risk** (card ids start `geopolitics-`): `risk.conflict`, `risk.terror`, `risk.unrest`, `risk.politics`, `risk.sanctions`, `risk.health`, `risk.climate`, `risk.systems`, `risk.macro_downturn`.
5. **Review pass** (after the streams finish, one agent): merge the streams into one file and run the self-check at the end of this brief. Send any thin answer back to its stream once: a nil finding resting on one source or no local-language search, a card for something that recurs every year unchanged, a missing base effect, or two cards counting the same effect.

Give each stream sub-agent this whole brief plus its item list, and ask it to return only its own `drivers` and `coverage` entries. Streams share one browser, so let each use web search and page fetching first and the browser for pages that need it. Only the review pass writes the final file.

## Checklist (answer every item)

| Id | Area | What to check |
| --- | --- | --- |
| `cal.remaining_months` | Events and place | Current year, remaining months: every event and closure in the months after the last actual month, against the same months last year (both directions: new this year, or held last year and not repeated) |
| `cal.recurring` | Events and place | Recurring events by month (trade fairs, congresses, festivals, sport, fashion weeks): date shifts between years, months gained or lost |
| `cal.rotations` | Events and place | Multi-year rotations: biennial and triennial fairs and congresses, events that rotate between cities, odd and even year cycles |
| `cal.mega_future` | Events and place | Mega-events awarded or bidding within the horizon (Olympics, World Cups, Euros, Expos, finals, Grand Departs, Eurovision, papal and royal events, Jubilees) |
| `cal.concerts` | Events and place | Stadium concert tours and large one-off shows announced for the horizon |
| `cal.base_effects` | Events and place | Base effects: one-offs in the last complete year and the current year that will not recur (they reduce growth the year after) |
| `cal.congress_pipeline` | Events and place | Association congresses booked for future years (convention bureau, ICCA), and convention venue capacity added or lost |
| `air.traffic` | Access, source markets, corporate and policy | Airport passenger trend against its history (latest months, forecasts) |
| `air.routes` | Access, source markets, corporate and policy | Routes and frequencies announced, cut or resumed; airline bases opened or closed; long-haul links |
| `air.capacity` | Access, source markets, corporate and policy | Airport capacity: caps, slot limits, runway or terminal projects, night bans, strikes |
| `rail.links` | Access, source markets, corporate and policy | Rail: high-speed lines, cross-border services, station works or closures, rail strikes |
| `cruise` | Access, source markets, corporate and policy | Cruise calls and turnarounds, and limits on them |
| `src.mix` | Access, source markets, corporate and policy | Source-market mix of room nights (domestic and top five international), from tourism statistics |
| `src.macro` | Access, source markets, corporate and policy | GDP, real incomes and consumer confidence in the top source markets against their history |
| `src.fx` | Access, source markets, corporate and policy | Exchange rates of the top source markets against the local currency |
| `src.costs` | Access, source markets, corporate and policy | Travel costs: airfares, fuel and energy prices |
| `src.longhaul` | Access, source markets, corporate and policy | Long-haul markets (US, Gulf, China, Japan, India): recovery, visa rules, flight access |
| `corp.employers` | Access, source markets, corporate and policy | Major employers, institutions and headquarters arriving, expanding or leaving; office return trends |
| `corp.travel` | Access, source markets, corporate and policy | Corporate travel budgets and policies in the main source companies and sectors |
| `corp.academic` | Access, source markets, corporate and policy | Universities, hospitals and research centres that generate visits |
| `pol.taxes` | Access, source markets, corporate and policy | Tourist taxes and VAT on accommodation: changes enacted or proposed |
| `pol.entry` | Access, source markets, corporate and policy | Entry rules: visas, ETIAS, UK ETA, EES border checks, and their timing |
| `pol.str` | Access, source markets, corporate and policy | Short-term rental rules, registration and enforcement; listing counts |
| `pol.overtourism` | Access, source markets, corporate and policy | Visitor management: entry fees, caps, bans on new tourist uses, cruise limits |
| `pol.labour` | Access, source markets, corporate and policy | Labour costs and rules that push rates (minimum wage, staffing shortages) |
| `place.attractions` | Events and place | Attractions, museums, stadiums and venues opening, closing or renovating |
| `place.works` | Events and place | Major construction and urban projects affecting access or appeal (metro lines, regeneration, road closures) |
| `place.image` | Events and place | Destination reputation: rankings, safety perception, cleanliness, crime, media coverage |
| `sup.pipeline_check` | Supply | Every pipeline project of 50 rooms or more: current status and opening date from public sources |
| `sup.missing` | Supply | Projects missing from the export (planning applications, brand announcements, conversions) |
| `sup.closures` | Supply | Closures, conversions to residential or student housing, and long-term temporary closures |
| `sup.reopenings` | Supply | Temporarily closed hotels: reopening dates and the tier they reopen at |
| `sup.repositioning` | Supply | Rebrands and repositionings that change a hotel's tier |
| `sup.alternative` | Supply | Aparthotels, serviced apartments, hostels and other lodging outside CoStar's count |
| `sup.policy` | Supply | Planning policy: hotel moratoriums, zoning, permits |
| `sup.tier_check` | Supply | Whether the market's average tier in config/markets is right |
| `risk.conflict` | Risk | Wars and conflicts affecting air routes, energy prices or travel sentiment |
| `risk.terror` | Risk | Terrorism threat level and recent incidents |
| `risk.unrest` | Risk | Civil unrest and strikes (national, transport, hotel sector) |
| `risk.politics` | Risk | Elections, government instability, policy shocks |
| `risk.sanctions` | Risk | Sanctions and their effect on source markets or air space |
| `risk.health` | Risk | Public health: outbreaks, advisories |
| `risk.climate` | Risk | Weather and climate: heatwaves, floods, snow reliability, wildfire |
| `risk.systems` | Risk | Systemic outages: air traffic control, IT, cyber attacks on travel infrastructure |
| `risk.macro_downturn` | Risk | Recession risk in the market's economy and top source markets |

## Sizing with calculators

Give inputs and let the forecast compute the size; the page shows the arithmetic. Any input can be a single number or [low, most likely, high]. Every input is either sourced (in evidence) or labelled an assumption in `magnitude_note`. Put `calculator` on the card and leave `magnitude` out.

- `event`: attendees, out_of_town_share, nights, guests_per_room, hotel_share, capture_share, displacement_share, adr_compression, months; or event_type (mega_multi_sport, major_football_tournament, rugby_world_cup, year_long_religious) with months to use a measured analog when attendance is not published.
- `air_capacity`: seats (extra seats a year), load_factor, visitor_share, nights, guests_per_room, hotel_share, capture_share, already_in_trend_share.
- `source_market`: share_of_demand, and change_in_arrivals, or driver_change with elasticity; optional adr_sensitivity.
- `price_change`: price_change (e.g. a tax as a share of the room rate), elasticity, absorbed_by_hotels.
- `competing_supply`: units_change (e.g. -800 short-term rental units), unit_occupancy, shift_to_hotels, capture_share.

Defaults the forecast uses when an input is left out:

- Olympic-scale events: occupancy in event months: 0.0393
- Olympic-scale events: ADR in event months: 0.5124
- Major football tournaments: occupancy in event months: 0.019
- Major football tournaments: ADR in event months: 0.1336
- Rugby World Cup: occupancy in event months: -0.0317
- Rugby World Cup: ADR in event months: -0.0115
- Year-long religious events (Jubilee): occupancy, whole year: -0.0185
- Year-long religious events (Jubilee): ADR, whole year: 0.0398
- Guests per occupied room: 1.6
- Share of overnight visitors who stay in hotels: 60%
- Events: share of attendees from out of town: 60%
- Events: nights per out-of-town attendee: 2
- Events: share of normal demand in event months that is displaced: 2%
- Air: seat load factor: 82%
- Air: share of arriving passengers who are visitors: 50%
- Air: nights per visitor: 3
- Hotel demand price elasticity: -0.6

Without a calculator, give `magnitude` as `{low, mode, high, unit}`: unit `pct_of_level` for demand and ADR (1.0 = +1% of that year's level), `rooms` for supply, `multiplier` for `shock_probability` (1.5 = 50% more likely than the market's usual chance of a downturn).

## Chances

Give `probability` (0 to 1), or `event_status` and let the ladder set it: rumour 17.5%, credible report 40%, announced 80%, tickets on sale 90%, held 100%, cancelled 0%.

## Output: one file

Save one file named `drivers-<market-slug>.yaml` with two lists, `drivers` and `coverage`, in this format:

```yaml
drivers:
  # An event sized from organiser figures (event calculator)
  - id: events-maison-objet-jan-2028
    title: Maison&Objet moves from January to February in 2028
    category: mice               # air_access supply events macro fx geopolitical policy corporate mice leisure_sentiment regulation infrastructure other
    affects: demand              # demand | adr | supply | shock_probability
    timing: {start_year: 2028, months: [1]}
    shape: one_off               # one_off (that year, pays back next year) | temporary (+ decay_years) | step (permanent)
    event_status: announced      # rumour | credible_report | announced | tickets_on_sale | held | cancelled
    calculator:
      type: event
      inputs: {attendees: [70000, 80000, 90000], out_of_town_share: 0.55, nights: 2.5, capture_share: 0.06, months: [1],
               adr_compression: [0.02, 0.05, 0.08]}
    magnitude_note: Attendance and out-of-town share from the organiser's 2026 report; capture share is an assumption.
    mechanism_chain:
      - {order: 1, sign: "-", effect: January loses the fair's room nights}
      - {order: 2, sign: "-", effect: Rate compression in January eases}
    already_in_baseline: partial
    baseline_overlap_note: The fair recurs every year (in the baseline); only the month shift is new.
    evidence:
      - {claim: The 2028 edition runs 3 to 7 February., url: "https://...", publisher: Organiser, published: "2026-10-02", accessed: "2026-11-10"}
      - {claim: Dates confirmed by the convention centre calendar., url: "https://...", publisher: Venue, published: "2026-10-05", accessed: "2026-11-10"}
    confidence: medium
    status: proposed

  # New routes (air_capacity calculator)
  - id: demand-new-longhaul-routes-2027
    title: Two new long-haul routes from spring 2027
    category: air_access
    affects: demand
    shape: step
    timing: {start_year: 2027, peak_year: 2028}
    probability: 0.8
    calculator: {type: air_capacity, inputs: {seats: [250000, 300000, 350000], capture_share: 0.04, already_in_trend_share: 0.3}}
    already_in_baseline: partial
    baseline_overlap_note: The airport adds routes every year; 30% of these seats are taken as normal growth.
    evidence: [...]
    status: proposed

  # A stated size (no calculator), and a risk card
  - id: demand-tourist-tax-2027
    title: Tourist tax doubles from January 2027
    category: policy
    affects: demand
    shape: step
    timing: {start_year: 2027}
    probability: 0.9
    calculator: {type: price_change, inputs: {price_change: 0.012, absorbed_by_hotels: 0.3}}
    already_in_baseline: none
    baseline_overlap_note: New rate; not in history.
    evidence: [...]
    status: proposed
  - id: geopolitics-strike-risk-2027
    title: Pension reform strikes raise disruption risk in 2027
    category: geopolitical
    affects: shock_probability
    timing: {start_year: 2027}
    probability: 0.4
    magnitude: {low: 1.1, mode: 1.3, high: 1.6, unit: multiplier}
    already_in_baseline: partial
    baseline_overlap_note: Strikes recur; the multiplier covers only the risk above the usual rate.
    evidence: [...]
    status: proposed

coverage:
  - item: cal.recurring
    status: card                 # card | no_material_effect | not_applicable | could_not_verify
    cards: [events-maison-objet-jan-2028]
    finding: One date shift found; all other recurring fairs keep their months.
    sources: [{publisher: Convention bureau, url: "https://...", accessed: "2026-11-10"}]
    searches: ["salons paris 2027 2028 dates", "trade fair calendar paris 2028"]
  - item: risk.climate
    status: no_material_effect
    finding: No climate risk beyond the historical pattern; heat waves are already in the summer history.
    sources: [{publisher: National weather service, url: "https://...", accessed: "2026-11-10"}]
    searches: ["canicule paris tourisme impact", "paris heatwave hotel demand"]
```

Rules for the file:
- `id`: lowercase words joined by hyphens, unique, starting with the stream's prefix (`events-`, `demand-`, `supply-`, `geopolitics-`).
- `timing.months`: set it whenever the effect falls in specific months; it drives the monthly roll-up and the current year.
- `already_in_baseline` (none, partial, full) with `baseline_overlap_note` explaining what the long-run trend already holds.
- Supply cards: `affects: supply`, `magnitude` in rooms, and `tier` (Luxury, Upper Upscale, Upscale, Upper Midscale, Midscale, Economy).
- Every card has `status: proposed` and at least one evidence item with url, publisher, published and accessed.
- `coverage` has an entry for every checklist id. A nil finding still lists the sources checked and the searches run. `could_not_verify` says what was tried.

## Before saving: self-check

- Every checklist id appears in `coverage`; every card id named in coverage exists in `drivers`.
- No card double-counts another, and no card covers something that recurs every year unchanged.
- One-offs in the last two years that will not recur are included (they reduce growth the year after).
- The remaining months of the current year were checked against the same months last year.
- Positive and negative effects of the same event appear together (displacement, payback).
- Sizes stay within their analogs, or `magnitude_note` says why not.

## Then

Drop the file into the Drivers slot on the forecast page and run. The page lists any card it cannot use and any checklist item left unanswered, and shows every driver's line in each year's ledger.
