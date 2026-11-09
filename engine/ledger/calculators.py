"""Layer 3 sizing calculators: turn a driver card's stated inputs into an effect on annual demand and ADR.

Every calculator returns the effect as a share of the year's level (0.006 = +0.6%), a range, and the
working written out with the numbers used, so the size of every driver can be checked by hand.
Inputs may be a number or [low, most likely, high]; the range is the 10th to 90th percentile of 4,000
draws (triangular), with a fixed seed. Defaults come from the calibration registry.

Monthly roll-up: an effect in given months becomes an annual effect by weighting with those months'
share of the market's annual demand (for occupancy) or revenue (for ADR), from its own last three
complete years.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TYPES = ("event", "air_capacity", "source_market", "price_change", "competing_supply")


@dataclass
class MarketContext:
    annual_demand: float                 # room nights sold, last complete year
    demand_share: dict[int, float]       # month -> share of annual demand
    revenue_share: dict[int, float]      # month -> share of annual revenue
    adr: float
    currency: str


@dataclass
class CalcResult:
    demand: tuple[float, float, float] = (0.0, 0.0, 0.0)    # low, mid (median), high; share of annual demand
    adr: tuple[float, float, float] = (0.0, 0.0, 0.0)
    demand_mean: float = 0.0
    adr_mean: float = 0.0
    working: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Draw:
    def __init__(self, seed_key: str, n: int = 4000):
        self.rng = np.random.default_rng([20261109, zlib.crc32(seed_key.encode())])
        self.n = n

    def __call__(self, x) -> np.ndarray:
        if isinstance(x, (list, tuple)):
            lo, mo, hi = (float(v) for v in x)
            return np.full(self.n, mo) if hi <= lo else self.rng.triangular(lo, mo, hi, self.n)
        return np.full(self.n, float(x))


def _show(x, pct=False, dp=0):
    def one(v):
        return f"{v * 100:.{max(dp, 0)}f}%" if pct else f"{v:,.{dp}f}"
    if isinstance(x, (list, tuple)):
        return f"{one(x[1])} ({one(x[0])} to {one(x[2])})"
    return one(float(x))


def _summ(arr) -> tuple[tuple[float, float, float], float]:
    return (float(np.percentile(arr, 10)), float(np.percentile(arr, 50)), float(np.percentile(arr, 90))), float(arr.mean())


def _months(inputs: dict) -> list[int]:
    m = inputs.get("months") or list(range(1, 13))
    return [int(x) for x in m]


def event(inp: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    d = _Draw(key)
    months = _months(inp)
    dshare = sum(ctx.demand_share.get(m, 1 / 12) for m in months)
    rshare = sum(ctx.revenue_share.get(m, 1 / 12) for m in months)
    mnames = ", ".join(MONTHS[m - 1] for m in months)
    out = CalcResult()
    etype = inp.get("event_type")
    rng_share = float(R.get("events.analog_range", 0.5))

    def analog(name, default=0.0):
        v = R.get(f"events.{etype}.{name}") if etype else None
        return default if v is None else float(v)

    # Demand
    if inp.get("attendees") is not None:
        att = d(inp["attendees"])
        oot = d(inp.get("out_of_town_share", R["calc.out_of_town_share"]))
        nights = d(inp.get("nights", R["calc.event_nights"]))
        gpr = d(inp.get("guests_per_room", R["calc.guests_per_room"]))
        hshare = d(inp.get("hotel_share", R["calc.hotel_share"]))
        if inp.get("capture_share") is None:
            out.warnings.append("No capture_share given: all hotel nights are assumed to fall in this market.")
        cap = d(inp.get("capture_share", 1.0))
        disp = d(inp.get("displacement_share", R["calc.displacement_share"]))
        gross = att * oot * nights / gpr * hshare * cap
        displaced = disp * dshare * ctx.annual_demand
        dem = (gross - displaced) / ctx.annual_demand
        out.working += [
            f"{_show(inp['attendees'])} attendees x {_show(inp.get('out_of_town_share', R['calc.out_of_town_share']), True)} from out of town "
            f"x {_show(inp.get('nights', R['calc.event_nights']), dp=1)} nights / {_show(inp.get('guests_per_room', R['calc.guests_per_room']), dp=1)} guests per room "
            f"x {_show(inp.get('hotel_share', R['calc.hotel_share']), True)} in hotels x {_show(inp.get('capture_share', 1.0), True)} in this market "
            f"= {np.median(gross):,.0f} room nights.",
            f"Less regular demand displaced in {mnames}: {_show(inp.get('displacement_share', R['calc.displacement_share']), True)} of "
            f"{dshare * ctx.annual_demand:,.0f} room nights = {np.median(displaced):,.0f}.",
            f"Net {np.median(gross - displaced):,.0f} room nights = {np.median(dem) * 100:+.2f}% of annual demand ({ctx.annual_demand:,.0f})."]
    else:
        occ = analog("occ") if inp.get("occupancy_effect") is None else None
        eff = d(inp["occupancy_effect"]) if inp.get("occupancy_effect") is not None else \
            d([occ * (1 - rng_share), occ, occ * (1 + rng_share)] if occ >= 0 else [occ * (1 + rng_share), occ, occ * (1 - rng_share)])
        dem = eff * dshare
        src = "stated" if inp.get("occupancy_effect") is not None else f"analog for {etype}"
        out.working.append(f"Occupancy in {mnames} {np.median(eff) * 100:+.1f}% ({src}) x {dshare * 100:.1f}% of annual demand "
                           f"in those months = {np.median(dem) * 100:+.2f}% of annual demand.")
    # ADR
    comp_in = inp.get("adr_compression")
    if comp_in is None:
        c0 = analog("adr")
        comp = d([c0 * (1 - rng_share), c0, c0 * (1 + rng_share)] if c0 >= 0 else [c0 * (1 + rng_share), c0, c0 * (1 - rng_share)])
        src = f"analog for {etype}" if etype else "none stated"
    else:
        comp, src = d(comp_in), "stated"
    adr = comp * rshare
    out.working.append(f"ADR in {mnames} {np.median(comp) * 100:+.1f}% ({src}) x {rshare * 100:.1f}% of annual revenue in those months "
                       f"= {np.median(adr) * 100:+.2f}% on annual ADR.")
    if etype and len(months) < 12:
        for side, mth in (("before", min(months) - 1), ("after", max(months) + 1)):
            v = analog(f"adr_month_{side}")
            if 1 <= mth <= 12 and abs(v) > 0.005:
                sh = ctx.revenue_share.get(mth, 1 / 12)
                adr = adr + v * sh
                out.working.append(f"Month {side} ({MONTHS[mth - 1]}): ADR {v * 100:+.1f}% (analog) x {sh * 100:.1f}% of revenue "
                                   f"= {v * sh * 100:+.2f}%.")
    out.demand, out.demand_mean = _summ(dem)
    out.adr, out.adr_mean = _summ(adr)
    return out


def air_capacity(inp: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    d = _Draw(key)
    seats = d(inp["seats"])
    lf = d(inp.get("load_factor", R["calc.load_factor"]))
    vis = d(inp.get("visitor_share", R["calc.visitor_share"]))
    nights = d(inp.get("nights", R["calc.visit_nights"]))
    gpr = d(inp.get("guests_per_room", R["calc.guests_per_room"]))
    hs = d(inp.get("hotel_share", R["calc.hotel_share"]))
    cap = d(inp.get("capture_share", 1.0))
    trend = d(inp.get("already_in_trend_share", 0.0))
    out = CalcResult()
    if inp.get("capture_share") is None:
        out.warnings.append("No capture_share given: all visitors' hotel nights are assumed to fall in this market.")
    rn = seats * lf * vis * nights / gpr * hs * cap * (1 - trend)
    dem = rn / ctx.annual_demand
    out.working = [f"{_show(inp['seats'])} extra seats a year x {_show(inp.get('load_factor', R['calc.load_factor']), True)} load factor "
                   f"x {_show(inp.get('visitor_share', R['calc.visitor_share']), True)} visitors x {_show(inp.get('nights', R['calc.visit_nights']), dp=1)} nights "
                   f"/ {_show(inp.get('guests_per_room', R['calc.guests_per_room']), dp=1)} guests per room x {_show(inp.get('hotel_share', R['calc.hotel_share']), True)} in hotels "
                   f"x {_show(inp.get('capture_share', 1.0), True)} in this market x {_show(1 - np.median(trend), True)} above the airport's normal growth "
                   f"= {np.median(rn):,.0f} room nights, {np.median(dem) * 100:+.2f}% of annual demand."]
    out.demand, out.demand_mean = _summ(dem)
    return out


def source_market(inp: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    d = _Draw(key)
    share = d(inp["share_of_demand"])
    out = CalcResult()
    if inp.get("change_in_arrivals") is not None:
        chg = d(inp["change_in_arrivals"])
        how = f"arrivals from {inp.get('source', 'the source market')} change {_show(inp['change_in_arrivals'], True)}"
    else:
        drv = d(inp["driver_change"])
        el = d(inp.get("elasticity", 1.0))
        chg = drv * el
        how = (f"{inp.get('driver', 'the driver')} changes {_show(inp['driver_change'], True)} x elasticity "
               f"{_show(inp.get('elasticity', 1.0), dp=2)} = arrivals {np.median(chg) * 100:+.1f}%")
    dem = share * chg
    out.working = [f"{inp.get('source', 'Source market')} is {_show(inp['share_of_demand'], True)} of room nights; {how}; "
                   f"effect {np.median(dem) * 100:+.2f}% of annual demand."]
    if inp.get("adr_sensitivity") is not None:
        a = dem * d(inp["adr_sensitivity"])
        out.adr, out.adr_mean = _summ(a)
        out.working.append(f"ADR moves {_show(inp['adr_sensitivity'], dp=2)} x the demand effect = {np.median(a) * 100:+.2f}%.")
    out.demand, out.demand_mean = _summ(dem)
    return out


def price_change(inp: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    d = _Draw(key)
    chg = d(inp["price_change"])                                  # e.g. a tourist tax as a share of the room rate
    el = d(inp.get("elasticity", R["calc.price_elasticity"]))
    absorb = d(inp.get("absorbed_by_hotels", 0.0))                # share of the change hotels absorb in their rate
    dem = el * chg * (1 - absorb)
    adr = -chg * absorb
    out = CalcResult()
    out.working = [f"Price to guests changes {_show(inp['price_change'], True)}; hotels absorb {_show(inp.get('absorbed_by_hotels', 0.0), True)} "
                   f"in their rate (ADR {np.median(adr) * 100:+.2f}%); demand moves elasticity "
                   f"{_show(inp.get('elasticity', R['calc.price_elasticity']), dp=2)} x {np.median(chg * (1 - absorb)) * 100:+.1f}% "
                   f"= {np.median(dem) * 100:+.2f}%."]
    out.demand, out.demand_mean = _summ(dem)
    out.adr, out.adr_mean = _summ(adr)
    return out


def competing_supply(inp: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    d = _Draw(key)
    units = d(inp["units_change"])                                # e.g. -800 short-term rental units removed
    occ = d(inp.get("unit_occupancy", 0.6))
    conv = d(inp.get("shift_to_hotels", 0.3))
    cap = d(inp.get("capture_share", 1.0))
    rn = -units * 365 * occ * conv * cap
    dem = rn / ctx.annual_demand
    out = CalcResult()
    out.working = [f"{_show(inp['units_change'])} competing units x 365 nights x {_show(inp.get('unit_occupancy', 0.6), True)} occupied "
                   f"x {_show(inp.get('shift_to_hotels', 0.3), True)} of their guests shifting to hotels x {_show(inp.get('capture_share', 1.0), True)} "
                   f"in this market = {np.median(rn):+,.0f} room nights, {np.median(dem) * 100:+.2f}% of annual demand."]
    out.demand, out.demand_mean = _summ(dem)
    return out


CALCULATORS = {"event": event, "air_capacity": air_capacity, "source_market": source_market, "price_change": price_change,
               "competing_supply": competing_supply}


def compute(calc: dict, ctx: MarketContext, R, key: str) -> CalcResult:
    t = calc.get("type")
    if t not in CALCULATORS:
        raise ValueError(f"Unknown calculator {t!r}; use one of {', '.join(CALCULATORS)}")
    return CALCULATORS[t](calc.get("inputs") or {}, ctx, R, key)
