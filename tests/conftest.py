from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from allram.state import StateStore

    monkeypatch.setenv("ALLRAM_HOME", str(tmp_path / "data"))
    return StateStore(tmp_path / "data")
