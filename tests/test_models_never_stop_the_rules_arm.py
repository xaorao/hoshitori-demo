"""A model that answers anything cannot stop the rules arm (build-brief unit B7, item 2).

The league commits the rules arm's forecasts and the model arms' in one batch. A model is the one part of a cycle that is not ours: it can
answer with text that is not JSON, with JSON that is not a chat answer, with a chat answer whose content is not a decision, with a decision
whose numbers are not numbers, with nothing, slowly, or not at all. The property, over many such behaviours drawn by ``hypothesis``:

* the cycle is committed, and its reason for abandoning is none;
* the rules arm's slots (symbol, horizon, contract, fate and reason) are exactly those of a cycle run with no model at all: no answer, however
  hostile, changes one of them;
* every slot a model arm was planned for ends in a fate the lifecycle knows, and none is missing;
* the ledger is still in the state OK.

The scripted model is the one of ``league_models_checks`` (a real client, a real registry, a real ledger and commitment); only its answer is
replaced, call by call, from the drawn script. ``derandomize`` makes the drawn set the same on every run, so a failure is repeatable; set
``HOSHITORI_PROPERTY_EXAMPLES`` to a larger number to look further.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import league, model_client
from hoshitori.capture import RawCapture
from tests.commit_checks import TickClock
from tests.league_checks import NOW, World
from tests.league_models_checks import Model, models, slots_of, world
from tests.mutation import mutant

EXAMPLES = int(os.environ.get("HOSHITORI_PROPERTY_EXAMPLES", "25"))
LEGAL = {"FORECAST", "VOID_GAP", "NO_CALL", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED"}
PLANNED = {("L1", 1), ("L1", 4), ("L1", 24), ("L1x", 4), ("L2", 4)}  # the model slots of one candidate: the arms and horizons of ARMS
FIELDS = (
    "decision",
    "p",
    "invalidations",
    "cites",
    "assertions",
    "summary",
    "claim",
    "support",
    "oppose",
    "uncertainty",
    "would_change_if",
)

values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=40),
    lambda kids: st.lists(kids, max_size=4) | st.dictionaries(st.text(max_size=12), kids, max_size=4),
    max_leaves=12,
)
decisions = st.dictionaries(st.sampled_from(FIELDS), values, max_size=6)
hostile_text = st.sampled_from(
    ["", " ", "NaN", "Infinity", '{"p": 1e999}', "```json\n{}\n```", "\x00", "[]", "null", "0", '"x"', "{" * 500, "a" * 20_000]
)
contents = st.one_of(st.text(max_size=300), hostile_text, decisions.map(json.dumps), values.map(json.dumps))


def chat(content: str) -> str:
    return json.dumps(
        {
            "id": "c",
            "model": "fake/model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        }
    )


bodies = st.one_of(contents.map(lambda c: chat(c).encode()), values.map(lambda v: json.dumps(v).encode()), st.binary(max_size=400))
behaviours = st.one_of(
    st.just(("good",)),
    st.just(("gone", model_client.Outcome.UNAVAILABLE)),  # a live fetcher reports no answer as UNAVAILABLE and as nothing else
    st.tuples(st.just("raw"), st.sampled_from([None, 200, 400, 429, 500, 503]), bodies),
)
scripts = st.lists(behaviours, min_size=1, max_size=24)


class Hostile(Model):
    """The scripted model, except that call number ``n`` behaves as the drawn script says (the script repeats)."""

    def __init__(self, w: World, script: list[tuple[Any, ...]]) -> None:
        super().__init__(w)
        self.script = script

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        good = super().__call__(args, stage, timeout_s)
        number = int(good.capture_id[len("cap-") :], 16)  # the call's own number, which is not racy when the arms run side by side
        step = self.script[(number - 1) % len(self.script)]
        if step[0] == "good":
            return good
        if step[0] == "gone":
            return model_client.Gone(step[1], "scripted")
        return RawCapture(good.capture_id, good.source, good.request, step[1], good.headers, step[2], good.t_send, good.received_at)


def rules_arm_slots(out: Any) -> list[tuple[Any, ...]]:
    return sorted((s.symbol, s.hours, s.contract, s.fate.value, s.reason) for s in slots_of(out, "L0"))


def _baseline() -> list[tuple[Any, ...]]:
    """The rules arm's slots in a cycle that has no model arm at all."""
    root = Path(tempfile.mkdtemp(prefix="hoshitori-property-baseline-"))
    try:
        w = World(root, ledger_clock=TickClock(NOW, 0.001), protocol={"snapshot_freshness_ms": 900_000})
        try:
            return rules_arm_slots(w.run(league))
        finally:
            w.close()
            w.captures.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


BASELINE = _baseline()


def test_the_baseline_is_a_real_cycle() -> None:
    assert BASELINE, "the cycle without a model made no rules-arm slot, so the property below would hold of nothing"
    assert {fate for *_, fate, _ in BASELINE} == {"FORECAST"}


def check_script(module: Any, script: list[tuple[Any, ...]]) -> None:
    """One cycle with the scripted hostile model, held to the whole property."""
    root = Path(tempfile.mkdtemp(prefix="hoshitori-property-"))
    try:
        w = world(root)
        try:
            out = w.run(module, models=models(Hostile(w, script)))
            assert (out.state.value, out.reason) == ("COMMITTED", None), (out.state, out.reason)
            assert rules_arm_slots(out) == BASELINE
            model_slots = [s for s in out.slots if s.arm_id != "L0"]
            assert {s.fate.value for s in model_slots} <= LEGAL
            assert {(s.arm_id, s.hours) for s in model_slots} == PLANNED
            assert w.ledger.state == "OK"
        finally:
            w.close()
            w.captures.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
@given(script=scripts)
def test_no_answer_stops_the_rules_arm(script: list[tuple[Any, ...]]) -> None:
    check_script(league, script)


def test_the_property_can_fail() -> None:
    """The property is a test of the cycle's handling of a model that fails: a league that lets a failed model answer end the cycle must
    not pass it, nor one that lets a hostile answer change what the rules arm decided."""
    abandons = mutant(
        "hoshitori.league",
        '    if result.verdict == "FAILED":\n        run.move(slot, lifecycle.Step(lifecycle.State.FAILED, finished, reason=result.reason))',
        '    if result.verdict == "FAILED":\n        raise LeagueError("a model failed")',
    )
    with pytest.raises(Exception, match="a model failed"):  # the league raises: the cycle is not committed
        check_script(abandons, [("gone", model_client.Outcome.UNAVAILABLE)])
    check_script(league, [("gone", model_client.Outcome.UNAVAILABLE)])  # and the real league passes the same script
