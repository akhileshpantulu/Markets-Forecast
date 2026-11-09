"""Calibration registry (config/calibration.yaml): values, user overrides, and the Assumptions-tab schema."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

from engine.config import CONFIG_DIR, load_yaml

TIERS = ["luxury", "upper_upscale", "upscale", "upper_midscale", "midscale", "economy"]
TIER_LABEL = {"luxury": "Luxury", "upper_upscale": "Upper Upscale", "upscale": "Upscale", "upper_midscale": "Upper Midscale",
              "midscale": "Midscale", "economy": "Economy"}


def tier_of(chain_scale) -> str | None:
    """CoStar chain scale label -> tier key."""
    if not isinstance(chain_scale, str):
        return None
    k = chain_scale.strip().lower().replace(" ", "_").replace("-", "_")
    return k if k in TIERS else None


def load_registry(path=None) -> dict:
    return load_yaml(path or CONFIG_DIR / "calibration.yaml")


@dataclass
class Values:
    """Effective values after user overrides."""
    v: dict
    reg: dict
    adjustments: list[str] = field(default_factory=list)
    overridden: set[str] = field(default_factory=set)

    def __getitem__(self, key):
        return self.v[key]

    def get(self, key, default=None):
        return self.v.get(key, default)

    def version(self) -> str:
        return hashlib.sha256(json.dumps(self.v, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _fmt(p: dict, v) -> str:
    if v is None:
        return "auto"
    if p.get("kind") == "select":
        return dict(map(tuple, p.get("options", []))).get(v, str(v))
    if p.get("kind") == "percent":
        return f"{float(v) * 100:.1f}%"
    if p.get("kind") == "points":
        return f"{float(v) * 100:+.1f} points"
    if p.get("kind") == "year":
        return str(int(v))
    return f"{float(v):g}"


def effective(settings: dict | None, reg: dict | None = None) -> Values:
    reg = copy.deepcopy(reg or load_registry())
    s = settings or {}
    v = {k: p.get("value") for k, p in reg["params"].items()}
    out = Values(v, reg)
    for k, new in s.items():
        if k not in reg["params"]:
            continue
        p = reg["params"][k]
        if new in ("", None) and p.get("value") is None:
            continue
        if new == p.get("value"):
            continue
        v[k] = new
        out.overridden.add(k)
        out.adjustments.append(f"{p['label']}: {_fmt(p, p.get('value'))} -> {_fmt(p, new)} ({p.get('status')} value overridden)")
    return out


def schema(reg: dict | None = None) -> list[dict]:
    """Assumptions-tab groups built from the registry."""
    reg = reg or load_registry()
    out = []
    for g in reg["groups"]:
        fields = []
        for k, p in reg["params"].items():
            if p.get("group") != g["id"]:
                continue
            fields.append({"key": k, "label": p["label"], "kind": p.get("kind", "number"), "min": p.get("min"),
                           "max": p.get("max"), "step": p.get("step"), "default": p.get("value"), "options": p.get("options"),
                           "status": p.get("status"), "evidence": p.get("evidence", "")})
        out.append({"id": g["id"], "title": g["title"], "help": g.get("help", ""), "fields": fields})
    return out
