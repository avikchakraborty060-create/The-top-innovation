import os
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own spool so the developer's ~/.skimread is never touched."""
    monkeypatch.setenv("SKIMREAD_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()
