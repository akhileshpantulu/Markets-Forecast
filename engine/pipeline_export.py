"""Pipeline export in the user's template format (Pipeline_Example.xlsx, 2026-11-10).

One sheet per market:
  - title "Pipeline Supply in <market>" and "HIGHLY CONFIDENTIAL"
  - the map with numbered pins (a PNG drawn by the page), anchored above the table
  - the project table: #, Name, Keys, Opening, Class, Owner, Operator, with the opening year beside it
  - pipeline supply by year: current supply, incoming keys, running total, growth, and the CAGR
  - the raw CoStar columns for the same projects, to the right (Date, Building Name ... Unit Only Sale)

Opening dates are CoStar's projected dates (Year and Month Built, or Year and Month Renov for reopenings):
the user's choice for this export. Formulas are kept so the sheet updates when edited.
"""

from __future__ import annotations

import io
import struct
from datetime import datetime

import pandas as pd

from engine.ingest.reader import RawTable

TEMPLATE_RAW_COLUMNS = [
    "Building Name", "Rooms", "Brand", "Parent Company", "Hotel Operator", "Actual Owner", "Market", "Submarket", "Class",
    "Scale", "Operation Type", "Constr Status", "Operation Status", "Expansion Rooms", "Expansion Status Date",
    "Expansion Status", "Address", "City", "Postcode", "Year Built", "Year Renov", "Hotel Open Date", "Data Participation",
    "Mtg Rooms", "Total Mtg Space Sq ft", "Max Contig Mtg Space Sq ft", "Hotel Location Type", "Secondary Type",
    "All Inclusive Rate", "All-Suites", "Primary Corridors", "Floors", "Parking Spaces", "Parking Spaces/Room", "Fund",
    "Flood Risk", "Flood Zone", "FEMA Map Date", "FEMA Map Identifier", "FIRM Id", "FIRM Panel Number", "SFHA",
    "Floodplain Area", "Month Built", "Sale Price", "Sale Price Not Disclosed", "Unit Only Sale",
]
PIPELINE_STAGES = ("under_construction", "under_renovation", "final_planning", "planning", "proposed", "deferred")
STAGE_LABEL = {"under_construction": "Under construction", "under_renovation": "Renovation (closed)",
               "final_planning": "Final planning", "planning": "Planning", "proposed": "Proposed", "deferred": "Deferred"}


def _raw_lookup(raw: RawTable | None) -> dict[str, dict]:
    """Property ID -> {header: first non-empty value} from the uploaded export."""
    if raw is None or "Property ID" not in raw.headers:
        return {}
    idx = raw.header_index()
    out = {}
    for row in raw.rows:
        pid = row[idx["Property ID"][0]] if idx["Property ID"][0] < len(row) else None
        if pid in (None, ""):
            continue
        rec = {}
        for h, pos in idx.items():
            vals = [row[i] for i in pos if i < len(row) and row[i] not in (None, "")]
            rec[h] = vals[0] if vals else None
        out[str(pid).strip()] = rec
    return out


def _num(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def sites(pipeline: pd.DataFrame | None, raw: RawTable | None, country: str = "") -> list[dict]:
    """Pipeline projects in opening order, numbered as on the map and in the table."""
    if pipeline is None or pipeline.empty:
        return []
    p = pipeline
    if p["snapshot_date"].nunique() > 1:
        p = p[p["snapshot_date"] == p["snapshot_date"].max()]
    p = p[p["stage"].isin(PIPELINE_STAGES) & (p["project_type"] != "renovation_open")]
    look = _raw_lookup(raw)
    rows = []
    for r in p.itertuples():
        rec = look.get(str(r.project_id), {})
        rooms = float(r.expansion_rooms) if r.project_type == "expansion" and not pd.isna(r.expansion_rooms) else float(r.rooms)
        opening = None if pd.isna(r.projected_open) else pd.Timestamp(r.projected_open)
        lat = _num(rec.get("Latitude"))
        lon = _num(rec.get("Longitude"))
        rows.append({
            "project_id": str(r.project_id), "name": r.name, "rooms": rooms, "stage": r.stage,
            "stage_label": STAGE_LABEL.get(r.stage, r.stage), "constr_status": rec.get("Constr Status") or r.stage_raw,
            "project_type": r.project_type, "opening": opening.strftime("%Y-%m-%d") if opening is not None else None,
            "year": int(opening.year) if opening is not None else None,
            "class": rec.get("Class") or (r.chain_scale if isinstance(r.chain_scale, str) else None),
            "owner": rec.get("Actual Owner"), "operator": rec.get("Hotel Operator"), "brand": rec.get("Brand"),
            "address": rec.get("Address"), "city": rec.get("City"), "postcode": rec.get("Postcode"), "country": country,
            "lat": lat, "lon": lon})
    rows.sort(key=lambda x: (x["opening"] or "9999", x["name"] or ""))
    for i, x in enumerate(rows, 1):
        x["n"] = i
    return rows


class _PngImage:
    """An openpyxl image from PNG bytes without Pillow (not bundled in the browser runtime)."""

    def __new__(cls, data: bytes, width_px: int | None = None):
        from openpyxl.drawing.image import Image

        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("The map image must be a PNG.")
        w, h = struct.unpack(">II", data[16:24])
        img = Image.__new__(Image)
        img.ref = io.BytesIO(data)
        img.format = "png"
        scale = (width_px / w) if width_px else 1.0
        img.width, img.height = w * scale, h * scale
        img.anchor = "A1"
        img._data = lambda: data
        return img


def build_workbook(market_name: str, sites_: list[dict], raw: RawTable | None, current_supply: float | None,
                   supply_as_of: str | None, first_year: int, map_png: bytes | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    title = "".join(ch for ch in market_name if ch not in '[]:*?/\\')[:31].upper() or "PIPELINE"
    ws.title = title
    f = lambda **k: Font(name="Arial", **({"size": 12} | k))  # noqa: E731
    navy = PatternFill("solid", fgColor="FF16365C")
    white_b = f(bold=True, color="FFFFFFFF")
    center = Alignment(horizontal="center", vertical="center")
    for col, w in {"A": 9.0, "B": 1.8, "C": 6.2, "D": 19.8, "E": 19.8, "F": 11.9, "G": 17.5, "H": 17.5, "I": 34.4, "J": 30.0,
                   "K": 1.8, "L": 9.0, "M": 14.0, "N": 18.0, "Y": 11.6}.items():
        ws.column_dimensions[col].width = w

    ws["B3"] = f"Pipeline Supply in {market_name}"
    ws["B3"].font = f(size=13, bold=True)
    ws["K2"] = "HIGHLY CONFIDENTIAL"
    ws["K2"].font = f(bold=True, color="FFC00000")
    ws["K2"].alignment = Alignment(horizontal="right")

    # Map above the table (C5:J25), as in the template.
    if map_png:
        img = _PngImage(map_png, width_px=760)
        img.anchor = "C5"
        ws.add_image(img)

    # Project table
    top = 26
    for col, text in zip("CDFGHIJ", ["#", "Name", "Keys", "Opening", "Class", "Owner", "Operator"]):
        c = ws[f"{col}{top}"]
        c.value, c.font, c.fill = text, white_b, navy
        c.alignment = center if col in "CFGH" else Alignment(vertical="center")
    for col in "E":
        ws[f"{col}{top}"].fill = navy
    first = top + 1
    for i, s in enumerate(sites_):
        r = first + i
        ws[f"C{r}"] = 1 if i == 0 else f"=+C{r - 1}+1"
        ws[f"D{r}"] = s["name"]
        ws[f"F{r}"] = s["rooms"]
        if s["opening"]:
            ws[f"G{r}"] = datetime.fromisoformat(s["opening"])
            ws[f"L{r}"] = f"=YEAR(G{r})"
        ws[f"H{r}"] = s["class"] or "N/A"
        ws[f"I{r}"] = s["owner"] or "N/A"
        ws[f"J{r}"] = s["operator"] or "N/A"
        for col in "CDFGHIJL":
            ws[f"{col}{r}"].font = f()
        ws[f"C{r}"].alignment = center
        ws[f"F{r}"].alignment = center
        ws[f"F{r}"].number_format = "#,##0"
        ws[f"G{r}"].number_format = "mmm yyyy"
        ws[f"G{r}"].alignment = center
        ws[f"H{r}"].alignment = center
    last = first + max(len(sites_), 1) - 1
    tot = last + 1
    ws[f"C{tot}"], ws[f"F{tot}"] = "TOTAL", f"=SUM(F{first}:F{last})"
    for col in "CF":
        ws[f"{col}{tot}"].font = f(bold=True)
    ws[f"F{tot}"].alignment = center
    ws[f"F{tot}"].number_format = "#,##0"

    # Pipeline supply by year
    hdr = tot + 2
    for col, text in zip("CDEFGH", ["Pipeline Supply by Year", None, None, "Incoming", "Total", "%"]):
        c = ws[f"{col}{hdr}"]
        c.value, c.font, c.fill = text, white_b, navy
        if col in "FGH":
            c.alignment = center
    cur = hdr + 1
    ws[f"C{cur}"], ws[f"G{cur}"] = "Current Supply", f"=+M{cur + 1}"
    ws[f"M{cur}"] = f"COSTAR {market_name.upper()} SUPPLY"
    ws[f"M{cur + 1}"] = current_supply
    ws[f"M{cur + 1}"].font = f(bold=True, color="FF0000FF")
    ws[f"M{cur + 1}"].number_format = '#,###;\\(#,###\\);"-"'
    if supply_as_of:
        ws[f"N{cur + 1}"] = f"AS OF {supply_as_of.upper()}"
    years = [s["year"] for s in sites_ if s["year"]]
    last_year = max([first_year + 5] + years)
    y_first = cur + 1
    for k, y in enumerate(range(first_year, last_year + 1)):
        r = y_first + k
        ws[f"C{r}"] = y if k == 0 else f"=+C{r - 1}+1"
        ws[f"F{r}"] = f"=SUMIFS($F${first}:$F${last},$L${first}:$L${last},C{r})"
        ws[f"G{r}"] = f"=+F{r}+G{r - 1}"
        ws[f"H{r}"] = f"=+G{r}/G{r - 1}-1"
        ws[f"C{r}"].alignment = Alignment(horizontal="left")
        for col in "FG":
            ws[f"{col}{r}"].number_format = "#,##0"
            ws[f"{col}{r}"].alignment = center
        ws[f"H{r}"].number_format = "0.0%"
        ws[f"H{r}"].alignment = center
    y_last = y_first + (last_year - first_year)
    t = y_last + 1
    ws[f"C{t}"] = "TOTAL / CAGR"
    ws[f"F{t}"] = f"=SUM(F{y_first}:F{y_last})"
    ws[f"G{t}"] = f"=+G{y_last}"
    ws[f"H{t}"] = f"=_xlfn.RRI(C{y_last}-{first_year - 1},G{cur},G{t})"
    for col in "CFGH":
        ws[f"{col}{t}"].font = f(bold=True)
    ws[f"F{t}"].number_format = ws[f"G{t}"].number_format = "#,##0"
    ws[f"H{t}"].number_format = "0.0%"
    for col in "FGH":
        ws[f"{col}{t}"].alignment = center

    # Raw CoStar columns (Y onward): Date, two spacer columns, then the template's columns.
    ws["Y3"] = "Date"
    start_col = 28                                           # AB
    for j, h in enumerate(TEMPLATE_RAW_COLUMNS):
        ws.cell(row=3, column=start_col + j, value=h).font = f()
    ws["Y3"].font = f()
    look = _raw_lookup(raw)
    for i, s in enumerate(sites_):
        r = 4 + i
        rec = look.get(s["project_id"], {})
        if s["opening"]:
            ws[f"Y{r}"] = datetime.fromisoformat(s["opening"])
            ws[f"Y{r}"].number_format = "mmm/yyyy"
            ws[f"Y{r}"].alignment = center
        for j, h in enumerate(TEMPLATE_RAW_COLUMNS):
            v = rec.get(h)
            if h == "Building Name" and v is None:
                v = s["name"]
            if h == "Rooms" and v is None:
                v = s["rooms"]
            ws.cell(row=r, column=start_col + j, value=v).font = f()
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 85
    wb.calculation.fullCalcOnLoad = True
    ws.print_area = f"B2:{get_column_letter(14)}{t + 1}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
