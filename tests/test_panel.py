"""Reader for the private data panel, tested on a synthetic panel written to a temp folder."""

import pandas as pd
import pytest

from engine.panel import geographies, submarket_monthly


def test_submarket_monthly_maps_to_internal_schema(tmp_path, monkeypatch):
    (tmp_path / "panel").mkdir()
    per = pd.date_range("2025-10-01", periods=4, freq="MS")
    df = pd.DataFrame({"slug": "testville-centre", "slice": "All", "period": per, "is_actual": [True, True, True, False],
                       "supply": [3100.0, 3000.0, 3100.0, None], "demand": [2000.0, 2100.0, 1900.0, None],
                       "revenue": [2e5, 2.2e5, 1.9e5, None], "inventory_rooms": [100.0, 100.0, 100.0, 110.0],
                       "under_construction_rooms": [10.0, 10.0, 10.0, 0.0], "source_file": "x.csv.gz"})
    df.to_parquet(tmp_path / "panel" / "performance_monthly.parquet")
    df.head(1)[["slug", "slice"]].to_csv(tmp_path / "panel" / "geographies.csv", index=False)
    monkeypatch.setenv("MARKETS_DATA_DIR", str(tmp_path))
    m = submarket_monthly("testville-centre")
    assert len(m) == 3                                         # the non-actual month is dropped
    assert m["occupancy"].iloc[1] == pytest.approx(0.7) and m["adr"].iloc[1] == pytest.approx(2.2e5 / 2100)
    assert m["available_rooms"].iloc[1] == pytest.approx(100.0)  # November: 3000 / 30
    assert len(geographies()) == 1
    with pytest.raises(KeyError):
        submarket_monthly("nowhere")


def test_missing_panel_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETS_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="marketsdata"):
        submarket_monthly("x")
