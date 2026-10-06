import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Never touch the real %APPDATA%\\Rika during tests."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return tmp_path / "appdata" / "Rika"
