"""Generate a synthetic CoStar-like dataset for tests only.

Nothing here is real data. The headers copy the layout of CoStar's hospitality
DataTable and property exports so the mappings are exercised end to end. The
numbers come from a seeded random process.

Run: python tests/fixtures/make_synthetic.py
"""

from __future__ import annotations

import calendar
from pathlib import Path

import numpy as np
import openpyxl

HERE = Path(__file__).resolve().parent
PERF_FILE = "DataTable - 2026-07-15T090000.000.xlsx"
PIPE_FILE = "CostarExport - 2026-07-15T090500.000.xlsx"
SYNTH_MARKET = "Synthetown"
SYNTH_SUBMARKET = "Old Quarter"

PERF_HEADERS = [
    "Period", "Inventory Rooms", "Existing Buildings", "Avg Rooms Per Building", "12 Mo Delivered Rooms",
    "12 Mo Inventory Growth", "12 Mo Opened Rooms", "12 Mo Opened Buildings", "Under Construction Rooms",
    "Under Construction Buildings", "12 Mo Supply", "12 Mo Supply Chg", "12 Mo Demand", "12 Mo Demand Chg",
    "12 Mo Revenue", "12 Mo Revenue Chg", "12 Mo Occupancy", "12 Mo Occupancy Chg", "12 Mo ADR", "12 Mo ADR Chg",
    "12 Mo RevPAR", "12 Mo RevPAR Chg", "Occupancy", "Occupancy Chg (YOY)", "ADR", "ADR Chg (YOY)", "RevPAR",
    "RevPAR Chg (YOY)", "Market Sale Price/Room", "12 Mo Sales Volume", "Market Yield", "YTD Occupancy", "YTD ADR",
    "YTD RevPAR", "YTD Occupancy Chg", "YTD ADR Chg", "YTD RevPAR Chg", "Supply", "Supply Chg (YOY)", "Demand",
    "Demand Chg (YOY)", "Revenue", "Revenue Chg (YOY)", "3 Mo Supply", "3 Mo Supply Chg", "3 Mo Demand",
    "3 Mo Demand Chg", "3 Mo Revenue", "3 Mo Revenue Chg", "3 Mo Occupancy", "3 Mo Occupancy Chg", "3 Mo ADR",
    "3 Mo ADR Chg", "3 Mo RevPAR", "3 Mo RevPAR Chg", "YTD Supply", "YTD Supply Chg", "YTD Demand",
    "YTD Demand Chg", "YTD Revenue", "YTD Revenue Chg",
]

PIPE_HEADERS = [
    "Building Name", "Rooms", "Brand", "Parent Company", "Hotel Operator", "Actual Owner", "Market", "Submarket",
    "Class", "Scale", "Operation Type", "Constr Status", "Operation Status", "Expansion Rooms",
    "Expansion Status Date", "Expansion Status", "Address", "City", "State", "Postcode", "Year Built", "Year Renov",
    "Hotel Open Date", "Data Participation", "Mtg Rooms", "Total Mtg Space Sq ft", "Max Contig Mtg Space Sq ft",
    "Hotel Location Type", "Secondary Type", "All Inclusive Rate", "All-Suites", "Primary Corridors", "Floors",
    "Parking Spaces", "Parking Spaces/Room", "Fund", "Flood Risk", "Flood Zone", "FEMA Map Date",
    "FEMA Map Identifier", "FIRM Id", "FIRM Panel Number", "SFHA", "Floodplain Area", "Month Built", "Month Renov",
    "Property ID", "Type", "Star Rating", "Green Rating", "Status", "Hotel Grade", "NIA", "Sq ft Avail",
    "Min Rent/Sq ft/yr", "Max Rent/Sq ft/yr", "Rent Estimated", "Rent Not Disclosed", "Landlord Representative",
    "Landlord Rep Contact", "Submarket Cluster", "County", "Country", "Continent", "Subcontinent", "Sales Company",
    "Sales Contact", "Sale Price", "Sale Price Not Disclosed", "Unit Only Sale", "Min Price/Sq ft",
    "Max Price/Sq ft", "Price/Area Not Disclosed", "Price/Room", "Sale Status", "Sale Method", "Last Sale Date",
    "Last Sale Price", "Land(ac)", "% Leased", "Typ Fl(Sq ft)", "Parking Ratio", "Tenancy", "Building Park",
    "Map Page", "Zoning", "Min Asking Rent/yr", "Max Asking Rent/yr", "Asking Rent Not Disclosed", "Actual Owner",
    "Constr Start", "Previous Brand", "Affiliation Date", "Proposed Brand", "Proposed Brand Executed Date",
    "Proposed Brand Start Date", "Proposed brand status", "Origination Amount", "Origination Date",
    "Maturity Date", "Originator", "Interest Rate", "Interest Rate Type", "Collateral Type", "Loan Type", "Unit",
]

MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]

# (name, rooms, class, constr status, operation status, year built, month built, year renov, month renov, id)
PIPELINE = [
    ("Synth Grand Reopening", 150, "Upper Upscale", "Under Renovation", "Temporarily Closed", 1910, "Jan", 2027, "Mar", "900001"),
    ("Synth Corner Inn", 50, "Midscale", "Under Renovation", "Temporarily Closed", 1960, "Jun", 2026, "Nov", "900002"),
    ("Synth Tower", 220, "Upscale", "Under Construction", None, 2027, "Sept", None, None, "900003"),
    ("Synth Lofts", 80, "Upper Midscale", "Under Construction", None, 2026, "Dec", None, None, "900004"),
    ("Synth Palace", 60, "Luxury", "Final Planning", None, 2028, "Apr", None, None, "900005"),
    ("Synth Garden", 120, "Upscale", "Proposed", None, 2029, None, None, None, "900006"),
    ("Synth Rooftop", 40, "Upper Upscale", "Under Renovation", None, 1985, "Mar", 2026, "Dec", "900007"),
]
CLOSED_ROOMS = 200  # rooms of the two temporarily closed renovations


def _months(start_year: int, end_year: int, end_month: int):
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            if y == end_year and m > end_month:
                break
            out.append((y, m))
    return out


def simulate(seed: int = 7):
    """Return a list of monthly dicts (oldest first) plus the number of inventory-only months."""
    rng = np.random.default_rng(seed)
    inv_only = _months(2003, 2004, 12)
    months = _months(2005, 2026, 6)
    season = np.array([0.62, 0.66, 0.75, 0.82, 0.86, 0.90, 0.85, 0.74, 0.90, 0.88, 0.76, 0.66])
    adr_season = np.array([0.85, 0.83, 0.95, 1.00, 1.05, 1.12, 1.02, 0.88, 1.10, 1.10, 0.97, 0.95])
    rooms = 5000.0
    level, g = 1.0, 0.0
    adr_level = 150.0
    rows = []
    for y, m in inv_only:
        rows.append({"y": y, "m": m, "inventory": rooms, "perf": False})
    for i, (y, m) in enumerate(months):
        if m == 1:
            g = 0.01 + 0.3 * (g - 0.01) + rng.normal(0, 0.03)
            if y == 2009:
                g -= 0.08
            level *= np.exp(g)
            adr_level *= np.exp(0.02 + 0.5 * g + rng.normal(0, 0.03))
        if m == 7 and y in (2010, 2016, 2022):
            rooms += 100
        days = calendar.monthrange(y, m)[1]
        closed = CLOSED_ROOMS if (y, m) >= (2026, 1) else 0
        avail = rooms - closed
        supply = float(avail * days)
        occ = float(np.clip(season[m - 1] * level * np.exp(rng.normal(0, 0.02)), 0.2, 0.97))
        demand = supply * occ
        adr = float(adr_level * adr_season[m - 1] * np.exp(rng.normal(0, 0.02)))
        revenue = demand * adr
        rows.append({"y": y, "m": m, "inventory": rooms, "perf": True, "supply": supply, "demand": demand,
                     "revenue": revenue, "occ": demand / supply, "adr": revenue / demand,
                     "revpar": revenue / supply})
    return rows


def _rel(a, b):
    return "-" if b in (None, 0) or a is None else a / b - 1


def write_performance(path: Path, seed: int = 7) -> None:
    rows = simulate(seed)
    perf = [r for r in rows if r["perf"]]
    by_key = {(r["y"], r["m"]): r for r in perf}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DataTable"
    ws.append(PERF_HEADERS)
    for r in reversed(rows):
        rec = {h: "-" for h in PERF_HEADERS}
        rec["Period"] = f"{MONTH_LABEL[r['m'] - 1]} {r['y']}"
        rec["Inventory Rooms"] = r["inventory"]
        rec["Existing Buildings"] = int(r["inventory"] // 40)
        rec["Avg Rooms Per Building"] = 40.0
        rec["Under Construction Rooms"] = 300
        rec["Under Construction Buildings"] = 2
        rec["12 Mo Delivered Rooms"] = "-"
        rec["12 Mo Opened Rooms"] = 0
        if r["perf"]:
            rec.update({"Supply": r["supply"], "Demand": r["demand"], "Revenue": r["revenue"], "Occupancy": r["occ"],
                        "ADR": r["adr"], "RevPAR": r["revpar"]})
            prev = by_key.get((r["y"] - 1, r["m"]))
            if prev:
                rec["Occupancy Chg (YOY)"] = _rel(r["occ"], prev["occ"])
                rec["ADR Chg (YOY)"] = _rel(r["adr"], prev["adr"])
                rec["RevPAR Chg (YOY)"] = _rel(r["revpar"], prev["revpar"])
                rec["Supply Chg (YOY)"] = _rel(r["supply"], prev["supply"])
                rec["Demand Chg (YOY)"] = _rel(r["demand"], prev["demand"])
            idx = perf.index(r)
            if idx >= 11:
                win = perf[idx - 11: idx + 1]
                rec["12 Mo Supply"] = sum(w["supply"] for w in win)
                rec["12 Mo Demand"] = sum(w["demand"] for w in win)
                rec["12 Mo Revenue"] = sum(w["revenue"] for w in win)
        else:
            for h in ("Supply", "Demand", "Revenue", "Occupancy", "ADR", "RevPAR"):
                rec[h] = None
        ws.append([rec[h] for h in PERF_HEADERS])
    wb.save(path)


def write_pipeline(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Properties"
    ws.append(PIPE_HEADERS)
    for name, rooms, cls, status, op, yb, mb, yr, mr, pid in PIPELINE:
        rec = {h: None for h in PIPE_HEADERS}
        rec.update({"Building Name": name, "Rooms": rooms, "Market": SYNTH_MARKET, "Submarket": SYNTH_SUBMARKET,
                    "Class": cls, "Scale": "Independent", "Operation Type": "Independent", "Constr Status": status,
                    "Operation Status": op, "Year Built": yb, "Month Built": mb, "Year Renov": yr,
                    "Month Renov": mr, "Property ID": pid, "Status": status, "Country": "Nowhere"})
        row = []
        for i, h in enumerate(PIPE_HEADERS):
            row.append(rec[h] if h != "Actual Owner" else ("Synthetic Owner" if i < 10 else None))
        ws.append(row)
    wb.save(path)


def main() -> None:
    write_performance(HERE / PERF_FILE)
    write_pipeline(HERE / PIPE_FILE)
    print("wrote", HERE / PERF_FILE, "and", HERE / PIPE_FILE)


if __name__ == "__main__":
    main()
