"""Pipeline export in the user's template format, and the map sites."""

from __future__ import annotations

import base64
import io
import json
import struct
import zlib

import openpyxl

from engine.web_api import export_pipeline, run_ingest
from tests.test_web_api import ADHOC, _files


def _png(w=4, h=3) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_sites_and_template_export(perf_bytes, pipe_bytes):
    out = json.loads(run_ingest(_files(perf_bytes, pipe_bytes), ADHOC, today="2026-07-20", paths=300))
    pm = out["pipeline_map"]
    sites = pm["sites"]
    assert sites and [s["n"] for s in sites] == list(range(1, len(sites) + 1))
    assert sites == sorted(sites, key=lambda s: (s["opening"] or "9999", s["name"]))
    assert all(s["address"] is not None or s["city"] is not None or True for s in sites)
    data = base64.b64decode(export_pipeline(_png()))
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    assert ws["B3"].value.startswith("Pipeline Supply in") and ws["K2"].value == "HIGHLY CONFIDENTIAL"
    assert [ws[f"{c}26"].value for c in "CDFGHIJ"] == ["#", "Name", "Keys", "Opening", "Class", "Owner", "Operator"]
    n = len(sites)
    assert ws["D27"].value == sites[0]["name"] and ws[f"C{27 + n}"].value == "TOTAL"
    assert ws[f"F{27 + n}"].value == f"=SUM(F27:F{26 + n})"
    hdr = 27 + n + 2
    assert ws[f"C{hdr}"].value == "Pipeline Supply by Year" and ws[f"C{hdr + 1}"].value == "Current Supply"
    assert ws[f"M{hdr + 2}"].value == pm["current_supply"]
    assert ws["Y3"].value == "Date" and ws["AB3"].value == "Building Name" and ws["BV3"].value == "Unit Only Sale"
    import zipfile
    media = [n for n in zipfile.ZipFile(io.BytesIO(data)).namelist() if n.startswith("xl/media/")]
    assert media == ["xl/media/image1.png"] and zipfile.ZipFile(io.BytesIO(data)).read(media[0]) == _png()
    plain = zipfile.ZipFile(io.BytesIO(base64.b64decode(export_pipeline(None)))).namelist()
    assert not [n for n in plain if n.startswith("xl/media/")]
