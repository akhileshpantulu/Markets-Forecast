"""Licensed-data guard.

While config/hosting.yaml says the repository is public, no CoStar data or
run output may be tracked by git. Spreadsheets, CSV and Parquet files are only
allowed under tests/fixtures/ (synthetic data).
"""

from __future__ import annotations

import subprocess

import pytest

from engine.config import CONFIG_DIR, REPO_ROOT, load_yaml

PROTECTED_DIRS = ("inbox/", "data/raw/", "data/processed/", "runs/")
DATA_EXT = (".xlsx", ".xlsm", ".xls", ".csv", ".parquet")


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached"], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [line for line in out.stdout.splitlines() if line]


def test_no_data_tracked_while_public():
    hosting = load_yaml(CONFIG_DIR / "hosting.yaml")
    files = _tracked()
    data_outside_fixtures = [f for f in files if f.lower().endswith(DATA_EXT) and not f.startswith("tests/fixtures/")]
    assert not data_outside_fixtures, f"data files tracked outside tests/fixtures: {data_outside_fixtures}"
    if hosting.get("repo_private"):
        return
    leaked = [f for f in files if f.startswith(PROTECTED_DIRS) and not f.endswith(".gitkeep")]
    assert not leaked, f"repository is public; these must not be tracked: {leaked}"


def test_fixture_spreadsheets_are_synthetic():
    from tests.fixtures.make_synthetic import SYNTH_MARKET
    for f in _tracked():
        if f.startswith("tests/fixtures/") and f.endswith(".xlsx"):
            data = (REPO_ROOT / f).read_bytes()
            from engine.ingest.reader import read_table
            t = read_table(data, filename=f.split("/")[-1])
            if "Market" in t.headers:
                i = t.headers.index("Market")
                assert {r[i] for r in t.rows} == {SYNTH_MARKET}
