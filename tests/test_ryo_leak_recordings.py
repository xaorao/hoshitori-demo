"""RYO's interpretations never reach the agents, shown on every real answer the service has given us (private recordings; skipped where
they are not on this machine).

Each recorded successful answer of the six tools is turned into evidence by the code the league uses (``ryo_evidence.items_from``). The
observation item, which the agents may read, must hold no field the table classes as an interpretation, and no verdict, plan, pick or
prose RYO wrote may appear among its values; every interpretation field that has a value must sit in the interpretation item, which only
arm R and the audit read. The same table decides both, so a field added to the answer that the table does not know is never read at all
(``test_ryo_suite_recordings`` shows the table covers every real answer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoshitori import ryo_evidence, ryo_fields
from hoshitori.ryo_envelope import read_rest_success
from tests.test_ryo import _live_capture
from tests.test_ryo_suite_recordings import SUCCESS, load, tool_of

pytestmark = pytest.mark.skipif(not SUCCESS, reason="the private suite recordings are not on this machine")


def _interpretation_text(value: object) -> list[str]:
    """The words RYO wrote in an interpretation field: text with a lower-case letter and more than three characters (a verdict, a label,
    prose). A number or a symbol in capitals (a pick such as 'AVAX') can equal an observation's value by chance and is no leak, so it is
    not counted; the path check above is the one that holds for every field."""
    if isinstance(value, str):
        return [value] if len(value) > 3 and any(ch.islower() for ch in value) else []
    if isinstance(value, list | tuple):
        return [text for item in value for text in _interpretation_text(item)]
    return []


@pytest.mark.parametrize("path", SUCCESS, ids=lambda p: f"{p.parent.name}-{p.name.split('.')[0]}")
def test_no_interpretation_reaches_the_observation_an_agent_reads(path: Path) -> None:
    rec = load(path)
    tool = tool_of(rec)
    request = rec["request"]["body"]
    made = ryo_evidence.items_from(_live_capture(rec, tool), tool, request, "leak-test")
    envelope = read_rest_success(rec["raw"], tool)
    walked = ryo_fields.walk(tool, envelope.data)
    interpretation_paths = {f.path for f in walked.fields if f.klass is ryo_fields.FieldClass.INTERPRETATION}
    observation_paths = {p for p, _ in made.observation.fields} | {p for p, _ in made.observation.unknown_fields}
    assert not observation_paths & interpretation_paths, sorted(observation_paths & interpretation_paths)
    written = {text for f in walked.fields if f.klass is ryo_fields.FieldClass.INTERPRETATION for text in _interpretation_text(f.value)}
    observed = {str(v) for _, v in made.observation.fields}
    assert not written & observed, sorted(written & observed)
    held = {p for p, _ in made.interpretation.fields} if made.interpretation is not None else set()
    valued = {f.path for f in walked.fields if f.klass is ryo_fields.FieldClass.INTERPRETATION and f.value is not None}
    assert valued <= held, sorted(valued - held)


def test_the_recordings_hold_real_interpretations_to_withhold() -> None:
    """The test above is not empty: the real answers carry verdicts, plans and prose that the observation item leaves out."""
    words: set[str] = set()
    for path in SUCCESS:
        rec = load(path)
        walked = ryo_fields.walk(tool_of(rec), read_rest_success(rec["raw"], tool_of(rec)).data)
        words |= {str(f.value) for f in walked.fields if f.klass is ryo_fields.FieldClass.INTERPRETATION and isinstance(f.value, str)}
    assert {"cautious", "neutral"} <= words, "RYO's own verdict words are among what is withheld"
    assert len(words) > 20, json.dumps(sorted(words))[:300]
