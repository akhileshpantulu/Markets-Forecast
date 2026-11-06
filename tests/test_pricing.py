"""RevPAR identity, the occupancy pricing rule and near-term inflation."""

from __future__ import annotations

import numpy as np
import pytest

from engine.forecast import apply_near_term, cpi_rate
from tests.test_drivers import _run, grow  # noqa: F401  (fixture)


@pytest.fixture(scope="module")
def base(grow):  # noqa: F811
    return _run(grow, cache={})


def test_revpar_p50_is_occupancy_times_adr(base):
    for r in base["summary"]:
        o, a = r["occ_growth"]["p50"], r["adr_growth"]["p50"]
        assert r["revpar_growth"]["p50"] == pytest.approx((1 + o) * (1 + a) - 1, abs=1e-12)
        assert r["revpar"]["p50"] == pytest.approx(r["occupancy"]["p50"] * r["adr"]["p50"], rel=1e-12)
        assert "p50_paths" in r["revpar_growth"]
    for s in base["explanation"]["years"][1:]:
        assert any(line.startswith("RevPAR") and "combined with ADR" in line for line in s["lines"])


def test_pricing_rule_signs_and_reporting(base):
    a = base["models"]["adr"]
    assert a["beta"] >= 0 and a["beta_change"] < 0 and a["beta_level"] <= 0 and a["beta_high"] <= 0
    assert a["high_threshold"] == pytest.approx(0.90)
    assert 0.3 < a["occ_ref"] < 1.0
    assert len(base["pricing_sensitivity"]["by_year"]) == len(base["summary"])
    b = base["explanation"]["bridge"][1]["adr"]
    assert {"downturn", "occ_change", "occ_level"} <= set(b)
    assert sum(v for k, v in b.items() if k != "total") == pytest.approx(b["total"], abs=1e-9)


def test_high_occupancy_dampens_rate(grow, base):  # noqa: F811
    # A threshold below the market's occupancy switches the extra dampening on in every year.
    damp = _run(grow, cache={}, settings={"adr.high_threshold": 0.5, "adr.high_dampening": -1.0})
    for r, b in zip(damp["summary"][1:], base["summary"][1:]):
        assert r["adr_growth"]["p50"] < b["adr_growth"]["p50"] - 0.01
        assert r["occ_growth"]["p50"] == pytest.approx(b["occ_growth"]["p50"], abs=1e-12)   # occupancy is untouched
    assert any("threshold" in x for x in damp["adjustments"])


def test_inverse_response_offsets_occupancy_gains(grow, base):  # noqa: F811
    inv = _run(grow, cache={}, settings={"adr.occ_change_response": -0.8})
    pos = _run(grow, cache={}, settings={"adr.occ_change_response": 0.8})
    cov = lambda o: np.mean([b["adr"]["occ_change"] for b in o["explanation"]["bridge"]])
    occ = np.mean([b["occupancy_total"] for b in base["explanation"]["bridge"]])
    # The mean change component has the opposite sign to the coefficient when occupancy grows beyond trend, and vice versa.
    assert np.sign(cov(inv)) != np.sign(cov(pos)) or cov(inv) == cov(pos) == 0
    assert occ == pytest.approx(np.mean([b["occupancy_total"] for b in inv["explanation"]["bridge"]]), abs=1e-12)


def test_near_term_inflation_overrides_imf_years_only():
    macro = {"cpi_history": {"values": {2024: 0.02, 2025: 0.03}},
             "cpi_projections": {"values": [{"year": y, "rate": 0.02} for y in range(2026, 2032)]}}
    nt = {"measure": "CPI, annual average", "values": [
        {"year": 2025, "rate": 0.09, "publisher": "X", "title": "T", "url": "https://x", "published": "2026-10-01", "accessed": "2026-11-06"},
        {"year": 2026, "rate": 0.031, "publisher": "X", "title": "T", "url": "https://x", "published": "2026-10-01", "accessed": "2026-11-06"},
        {"year": 2027, "rate": 0.034, "publisher": "X", "title": "T", "url": "https://x", "published": "2026-10-01", "accessed": "2026-11-06"},
        {"year": 2028, "rate": 0.05, "use": False}]}
    apply_near_term(macro, nt)
    assert cpi_rate(macro, 2025) == 0.03                 # history wins over a forecast
    assert cpi_rate(macro, 2026) == 0.031 and cpi_rate(macro, 2027) == 0.034
    assert cpi_rate(macro, 2028) == 0.02                 # switched off: IMF kept
    used = {v["year"]: v for v in macro["cpi_near_term"]["values"]}
    assert set(used) == {2026, 2027} and used[2027]["imf_rate"] == 0.02
