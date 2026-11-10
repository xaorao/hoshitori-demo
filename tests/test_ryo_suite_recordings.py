"""The metered probe suite of the live service (private recordings, skipped where they are not on this machine): every successful answer of
all six tools fits the field table, an answer for a section that was not asked for (``include_perp: false``) is read as absent fields, and
the adapter classifies each recorded error the way the cycle needs (invalid arguments, an unknown tool, the fan-out limit)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hoshitori import ryo, ryo_catalogue, ryo_fields
from hoshitori.ryo_envelope import read_rest_success
from tests import ryo_support
from tests.test_ryo import NOW, PIN, _live_capture

PASSES = (Path(ryo_support.LIVE_DIR), Path(ryo_support.LIVE_DIR).parent / "ryo_live_suite")  # the first pass and the second, days apart
FILES = sorted(p for d in PASSES if d.is_dir() for p in d.glob("plan_*.json"))
pytestmark = pytest.mark.skipif(not FILES, reason="the private suite recordings are not on this machine")


def load(path: Path) -> dict[str, Any]:
    rec: dict[str, Any] = json.loads(path.read_text())
    rec["raw"] = ryo_support.dumps(rec["response"]["body"])
    return rec


def tool_of(rec: dict[str, Any]) -> str:
    return str(rec["request"]["path"]).split("/")[2]


SUCCESS = [p for p in FILES if json.loads(p.read_text())["response"]["status"] == 200]
FAILED = [p for p in FILES if json.loads(p.read_text())["response"]["status"] != 200]


@pytest.mark.parametrize("path", SUCCESS, ids=lambda p: f"{p.parent.name}-{p.name.split('.')[0]}")
def test_every_successful_answer_fits_the_field_table(path: Path) -> None:
    rec = load(path)
    envelope = read_rest_success(rec["raw"], tool_of(rec))
    assert envelope.data_mode == "live" and envelope.status in ("ok", "partial")
    walked = ryo_fields.walk(tool_of(rec), envelope.data)
    left_out = [m for m in walked.missing if not m.startswith("derivatives")]
    assert left_out == []  # nothing the table names is missing from a real answer
    assert walked.unmapped == [], walked.unmapped  # and nothing the real answer holds is unknown to the table
    if "noperp" in path.name:
        assert len([m for m in walked.missing if m.startswith("derivatives")]) == 9  # a section that was not asked for is absent, not zero
        assert not any(f.path.startswith("derivatives") for f in walked.fields)
    else:
        assert not any(m.startswith("derivatives") for m in walked.missing)


EXPECTED = {
    "invalid_analyze_no_args": ("INVALID_ARGS", "VALIDATION_FAILED"),
    "invalid_analyze_number": (
        "NOT_FOUND",
        "VALIDATION_FAILED",
    ),  # the service answers a number as it answers an unknown symbol; the client never sends one
    "invalid_analyze_unknown_symbol": ("NOT_FOUND", "VALIDATION_FAILED"),  # "couldn't analyze X right now": not a defect of ours
    "invalid_compare_one_symbol": ("INVALID_ARGS", "VALIDATION_FAILED"),
    "invalid_monitor_1d": ("INVALID_ARGS", "VALIDATION_FAILED"),
    "invalid_unknown_tool": ("CONTRACT_DRIFT", "NOT_FOUND"),
}
LIMITED = ("RATE_LIMIT", "RATE_LIMITED")  # which of four simultaneous calls the fan-out limit refuses differs from pass to pass


@pytest.mark.parametrize("path", FAILED, ids=lambda p: f"{p.parent.name}-{p.name.split('.')[0]}")
def test_every_recorded_error_is_classified_as_the_cycle_needs(path: Path) -> None:
    rec = load(path)
    name = path.name.split(".")[0].removeprefix("plan_")
    error_class, code = LIMITED if name.startswith("parallel_") else EXPECTED[name]
    adapter = ryo.RyoAdapter(ryo_catalogue.load_pin(PIN))
    error = adapter.classify(_live_capture(rec, tool_of(rec)), NOW)
    assert error is not None and error.error_class.value == error_class
    assert rec["response"]["body"]["code"] == code
    assert rec["response"]["body"]["trace_id"] == error.trace_id


def test_the_suite_covers_the_six_tools_and_the_three_kinds_of_error() -> None:
    tools = {tool_of(load(p)) for p in SUCCESS}
    assert tools == set(ryo_support.SIX)
    names = {p.name.split(".")[0].removeprefix("plan_") for p in FAILED}
    assert {n for n in names if not n.startswith("parallel_")} == set(EXPECTED)
    for d in PASSES:  # in every pass the fan-out limit refused some of the four simultaneous calls and let some through
        calls = [json.loads(p.read_text())["response"]["status"] for p in d.glob("plan_parallel_*.json")]
        assert sorted(calls) in ([200, 200, 429, 429], [200, 200, 200, 429], [200, 429, 429, 429])
    assert len(SUCCESS) % 2 == 0 and len(FAILED) % 2 == 0  # two passes of the same plan
