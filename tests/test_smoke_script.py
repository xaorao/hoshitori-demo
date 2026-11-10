"""The smoke script names only test files that exist, and refuses when one is gone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import smoke


def test_every_listed_file_exists() -> None:
    assert smoke.FILES and all((smoke.ROOT / "tests" / f).is_file() for f in smoke.FILES)
    assert len(set(smoke.FILES)) == len(smoke.FILES)


def test_a_missing_file_is_refused(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(smoke, "FILES", ("test_nothing_here.py",))
    monkeypatch.setattr(smoke, "ROOT", Path(smoke.ROOT))
    assert smoke.main() == 2
    assert "test_nothing_here.py" in capsys.readouterr().err
