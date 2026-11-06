"""Fetch every source in config/external_sources.yaml into data/external/.

Each source produces <id>.body.<ext> (the raw response, unchanged) and
<id>.meta.json (URL, publisher, fetch time, HTTP status, SHA-256, size).
A failed fetch keeps the previous files and records the error in
_manifest.json. Run by .github/workflows/external-data.yml, which has open
internet access; the Claude session network may not.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from engine.config import CONFIG_DIR, REPO_ROOT, load_yaml

OUT = REPO_ROOT / "data" / "external"
MAX_BYTES = 25_000_000          # larger responses are not stored; add filters to the URL instead
USER_AGENT = "hotel-market-forecast/0.1 (+https://github.com/akhileshpantulu/Markets-Forecast)"
EXT = {"jsonstat": "json", "imf_datamapper": "json", "json": "json", "ine_json": "json", "ecb_csv": "txt"}


def fetch_one(src: dict, out: Path = OUT, retries: int = 3, timeout: int = 90) -> dict:
    ext = EXT.get(src["format"], "txt")
    body_path = out / f"{src['id']}.body.{ext}"
    meta_path = out / f"{src['id']}.meta.json"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(src["url"], headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(MAX_BYTES + 1)
                status, ctype = r.status, r.headers.get("Content-Type", "")
            if len(data) > MAX_BYTES:
                return {"id": src["id"], "ok": False, "error": f"response over {MAX_BYTES} bytes; not stored"}
            if ext == "json":
                json.loads(data)          # must parse
            body_path.write_bytes(data)
            meta = {"id": src["id"], "url": src["url"], "publisher": src.get("publisher"), "title": src.get("title"),
                    "format": src["format"], "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "http_status": status, "content_type": ctype, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "body_file": body_path.name}
            meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
            return {"id": src["id"], "ok": True, "bytes": len(data), "fetched_at": meta["fetched_at"]}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)
    return {"id": src["id"], "ok": False, "error": last_err}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    reg = load_yaml(CONFIG_DIR / "external_sources.yaml")
    results = [fetch_one(s) for s in reg["sources"]]
    manifest = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": results}
    (OUT / "_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for r in results:
        print(("ok    " if r["ok"] else "FAILED"), r["id"], r.get("bytes", ""), r.get("error", ""))
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
