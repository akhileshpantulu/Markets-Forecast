"""Read uploaded spreadsheets into a neutral table: headers plus raw rows.

Accepts a path or raw bytes so the same code runs in the browser.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl


@dataclass
class RawTable:
    filename: str
    sheet: str | None
    headers: list[str]
    rows: list[list]
    sha256: str
    other_sheets: list[str] = field(default_factory=list)

    def header_index(self) -> dict[str, list[int]]:
        idx: dict[str, list[int]] = {}
        for i, h in enumerate(self.headers):
            idx.setdefault(h, []).append(i)
        return idx

    @property
    def duplicate_headers(self) -> list[str]:
        return [h for h, pos in self.header_index().items() if len(pos) > 1]


class ReadError(ValueError):
    pass


def _clean_header(value) -> str:
    return "" if value is None else str(value).strip()


def read_table(source: Path | str | bytes, filename: str | None = None, sheet: str | None = None,
               header_row: int = 1) -> RawTable:
    if isinstance(source, (str, Path)):
        path = Path(source)
        data = path.read_bytes()
        filename = filename or path.name
    else:
        data = bytes(source)
        if filename is None:
            raise ReadError("filename is required when reading bytes")
    sha = hashlib.sha256(data).hexdigest()
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return _read_xlsx(data, filename, sheet, header_row, sha)
    if lower.endswith(".csv"):
        return _read_csv(data, filename, header_row, sha)
    raise ReadError(f"unsupported file type: {filename}")


def _read_xlsx(data: bytes, filename: str, sheet: str | None, header_row: int, sha: str) -> RawTable:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        all_rows = list(ws.iter_rows(values_only=True))
        others = [w.title for w in wb.worksheets if w.title != ws.title]
        title = ws.title
    finally:
        wb.close()
    if len(all_rows) < header_row:
        raise ReadError(f"{filename}: no header row")
    headers = [_clean_header(h) for h in all_rows[header_row - 1]]
    rows = [list(r) for r in all_rows[header_row:] if any(v is not None and str(v).strip() != "" for v in r)]
    return RawTable(filename, title, headers, rows, sha, others)


def _read_csv(data: bytes, filename: str, header_row: int, sha: str) -> RawTable:
    text = data.decode("utf-8-sig")
    all_rows = list(csv.reader(io.StringIO(text)))
    headers = [_clean_header(h) for h in all_rows[header_row - 1]]
    rows = [list(r) for r in all_rows[header_row:] if any(str(v).strip() for v in r)]
    return RawTable(filename, None, headers, rows, sha, [])
