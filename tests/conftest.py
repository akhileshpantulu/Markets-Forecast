from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from engine.config import REPO_ROOT, load_market_config
from engine.ingest.core import InputFile
from engine.ingest.mapping import load_mappings

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTH_SLUG = "synthetown-old-quarter"
PERF_FILE = "DataTable - 2026-07-15T090000.000.xlsx"
PIPE_FILE = "CostarExport - 2026-07-15T090500.000.xlsx"
TODAY = date(2026, 7, 20)


@pytest.fixture(scope="session")
def synth_cfg():
    return load_market_config(SYNTH_SLUG, FIXTURES / "config")


@pytest.fixture(scope="session")
def mappings():
    return load_mappings()


@pytest.fixture(scope="session")
def perf_bytes() -> bytes:
    return (FIXTURES / PERF_FILE).read_bytes()


@pytest.fixture(scope="session")
def pipe_bytes() -> bytes:
    return (FIXTURES / PIPE_FILE).read_bytes()


@pytest.fixture
def synth_files(perf_bytes, pipe_bytes):
    return [InputFile("performance", PERF_FILE, perf_bytes), InputFile("pipeline", PIPE_FILE, pipe_bytes)]


@pytest.fixture
def synth_root(tmp_path):
    """A throwaway repo root with config, mappings and a populated inbox."""
    root = tmp_path / "repo"
    (root / "config" / "markets").mkdir(parents=True)
    shutil.copy(FIXTURES / "config" / "markets" / f"{SYNTH_SLUG}.yaml", root / "config" / "markets")
    shutil.copy(REPO_ROOT / "config" / "pipeline_priors.yaml", root / "config")
    shutil.copytree(REPO_ROOT / "data" / "mappings", root / "data" / "mappings")
    inbox = root / "inbox" / SYNTH_SLUG
    (inbox / "performance").mkdir(parents=True)
    (inbox / "pipeline").mkdir(parents=True)
    (inbox / "optional").mkdir(parents=True)
    shutil.copy(FIXTURES / PERF_FILE, inbox / "performance" / PERF_FILE)
    shutil.copy(FIXTURES / PIPE_FILE, inbox / "pipeline" / PIPE_FILE)
    (inbox / "optional" / "notes.txt").write_text("Synthetic notes for tests.", encoding="utf-8")
    return root
