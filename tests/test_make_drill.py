"""The failure drills (``scripts/make_drill.py``): every drill runs the real league code with one thing broken, and every one must hold."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

from hoshitori import drill

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_drill.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_drill", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def drills() -> list[dict[str, object]]:
    return load().run_all()


def test_the_drills_are_these(drills: list[dict[str, object]]) -> None:
    assert [d["id"] for d in drills] == [
        "healthy-exchange",
        "exchange-fails-one-token",
        "exchange-clock-off",
        "collecting-is-slow",
        "evidence-too-old",
        "stamps-late",
        "start-too-late",
        "healthy-model",
        "model-late",
        "model-down",
        "process-killed",
        "process-killed-after-the-batch",
        "exchange-asks-for-a-pause",
        "healthy-ryo",
        "ryo-down-and-partial",
        "ryo-limits-the-rate",
    ]


def test_every_drill_held(drills: list[dict[str, object]]) -> None:
    assert [(d["id"], d["held"]) for d in drills if not d["held"]] == []


def test_what_the_drills_say_is_what_the_page_reads(drills: list[dict[str, object]]) -> None:
    read = drill.read_drills(json.dumps(drills).encode())
    assert [d.id for d in read] == [d["id"] for d in drills]
    assert all(d.held for d in read)


def test_a_drill_that_does_not_match_its_expectation_is_reported_as_not_held() -> None:
    module = load()
    observed = {"cycle": "Committed", "level": None, "slots": [["Open", 1]], "reasons": [], "forecasts": 1}
    wanted = {**observed, "forecasts": 2}
    assert (observed == wanted) is False
    raw = [
        {
            "id": "x",
            "title": "t",
            "injected": "i",
            "expected": "e",
            "observed": observed,
            "expected_observed": wanted,
            "held": observed == wanted,
        }
    ]
    assert drill.read_drills(json.dumps(raw).encode())[0].held is False
    assert module.FATE_WORDS["FAILED_LATE"] == "Too late"


def test_the_script_writes_a_file_the_page_can_read(tmp_path: Path) -> None:
    module = load()
    out = tmp_path / "drill.json"
    import sys

    saved = sys.argv
    sys.argv = ["make_drill.py", str(out)]
    try:
        assert module.main() == 0
    finally:
        sys.argv = saved
    assert len(drill.read_drills(out.read_bytes())) == 16
    sys.argv = ["make_drill.py"]
    try:
        assert module.main() == 2
    finally:
        sys.argv = saved


def _held(scenario: object) -> bool:
    with tempfile.TemporaryDirectory(prefix="drill-test-") as tmp:
        (Path(tmp) / "x").mkdir()
        observed, wanted, *_ = scenario(Path(tmp) / "x")  # type: ignore[operator]
    return bool(observed == wanted)


def test_the_restart_drill_does_not_hold_if_a_lost_cycle_is_not_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drill is a test of recovery: take recovery away and it must say so."""
    from hoshitori import recovery

    module = load()
    assert _held(module.process_killed_after_the_snapshot)
    monkeypatch.setattr(recovery, "close_lost", lambda records, protocol, source, now, judge=None: ((), ()))
    assert not _held(module.process_killed_after_the_snapshot)


def test_the_pause_drill_does_not_hold_if_the_wait_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drill measures the wait it sees: a gateway that retries at once must not pass it."""
    from hoshitori import deployment, governor, retry

    module = load()
    assert _held(module.exchange_asks_for_a_pause)
    real = retry.Retrier.after_failure
    monkeypatch.setattr(
        retry.Retrier, "after_failure", lambda self, now, error_class, retry_after_s=None: real(self, now, error_class, 0.0)
    )
    monkeypatch.setattr(deployment, "RetryPolicy", lambda: retry.RetryPolicy(rate_limit_default_wait_s=0.0))
    monkeypatch.setattr(governor.Governor, "pause", lambda self, now, seconds: None)
    assert not _held(module.exchange_asks_for_a_pause)


def test_the_ryo_drill_does_not_hold_if_a_zero_is_kept_as_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drill reports the gaps it finds in the partial answer: a reader that turns a degraded zero into a number must not pass it."""
    from hoshitori import ryo_evidence

    module = load()
    assert _held(module.ryo_is_down_and_partial)
    monkeypatch.setattr(ryo_evidence, "_is_zero", lambda field: False)
    assert not _held(module.ryo_is_down_and_partial)


def test_the_rate_limit_drill_does_not_hold_if_a_refused_read_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drill reads the failures the cycle reports: a cycle that swallows a refused read without naming it must not pass."""
    import dataclasses

    from hoshitori import ryo_collect

    module = load()
    assert _held(module.ryo_limits_the_rate)
    original = ryo_collect.collect_ryo

    def silent(*args: object, **kwargs: object) -> object:
        return dataclasses.replace(original(*args, **kwargs), failures=())  # type: ignore[arg-type, type-var]

    monkeypatch.setattr(ryo_collect, "collect_ryo", silent)
    assert not _held(module.ryo_limits_the_rate)
