"""Monthly to annual aggregation."""

from __future__ import annotations

import pandas as pd

from engine.ingest.periods import fiscal_year


def to_annual(monthly: pd.DataFrame, start_month: int = 1) -> pd.DataFrame:
    """Aggregate monthly performance to (fiscal) years.

    Supply, demand and revenue are summed; occupancy, ADR and RevPAR are
    recomputed from the sums. Inventory is the year-end value. A year is
    complete when all 12 months are present.
    """
    m = monthly.sort_values("period_start").copy()
    m["year"] = m["period_start"].map(lambda t: fiscal_year(t, start_month))
    g = m.groupby("year")
    out = pd.DataFrame({
        "supply": g["supply"].sum(),
        "demand": g["demand"].sum(),
        "revenue": g["revenue"].sum(),
        "months_observed": g["period_start"].count(),
        "inventory_rooms_end": g["inventory_rooms"].last() if "inventory_rooms" in m else None,
        "first_month": g["period_start"].min(),
        "last_month": g["period_start"].max(),
    }).reset_index()
    out["occupancy"] = out["demand"] / out["supply"]
    out["adr"] = out["revenue"] / out["demand"]
    out["revpar"] = out["revenue"] / out["supply"]
    out["complete"] = out["months_observed"] == 12
    out["market"] = m["market"].iloc[0]
    out["period_type"] = "year"
    out["period_start"] = pd.to_datetime(out["first_month"]).map(lambda t: t.replace(day=1))
    out["source_file"] = ";".join(sorted(m["source_file"].unique()))
    cols = ["market", "year", "period_start", "period_type", "supply", "demand", "occupancy", "adr", "revpar",
            "revenue", "months_observed", "complete", "inventory_rooms_end", "first_month", "last_month", "source_file"]
    return out[cols]
