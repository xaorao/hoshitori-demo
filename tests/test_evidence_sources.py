"""The evidence builders: every check passes on the real module, every small change is noticed, and the private RYO recordings turn into evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from hoshitori import canonical, evidence, evidence_sources, lineage, ryo_envelope, ryo_fields
from hoshitori.capture import RawCapture
from tests.evidence_sources_checks import failures
from tests.mutation_sweep import survivors
from tests.ryo_support import live_fixture


def test_real_module_passes_every_check() -> None:
    assert failures(evidence_sources) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.evidence_sources", "tests.evidence_sources_checks")
    assert total > 120, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"


LIVE_TOOLS = ("analyze_token", "deep_analysis", "scan_market", "monitor_market_sentiment_shift")
RECEIVED = datetime(2026, 10, 29, 13, 0, 0, tzinfo=UTC)


def built(tool: str) -> tuple[evidence_sources.RyoEvidence, ryo_fields.Walked, ryo_envelope.Envelope, RawCapture]:
    recording = live_fixture(f"call_{tool}_valid")
    if recording["response"]["status"] != 200:
        pytest.skip(f"the recording of {tool} is an error answer")
    envelope = ryo_envelope.read_rest_success(recording["raw"], tool)
    walked = ryo_fields.walk(tool, envelope.data)
    capture = RawCapture("cap-" + "c" * 32, "ryo", {"tool": tool}, 200, {}, recording["raw"], RECEIVED - timedelta(seconds=40), RECEIVED)
    return evidence_sources.ryo_items(capture, envelope, walked, "ryo-1"), walked, envelope, capture


@pytest.mark.parametrize("tool", LIVE_TOOLS)
def test_a_recorded_answer_becomes_evidence_that_loses_nothing(tool: str) -> None:
    result, walked, envelope, _ = built(tool)
    seen_obs = {p for p, _ in result.observation.fields} | {p for p, _ in result.observation.unknown_fields}
    seen_int = {p for p, _ in result.interpretation.fields} | {p for p, _ in result.interpretation.unknown_fields}
    for field in walked.fields:
        if field.klass is ryo_fields.FieldClass.META:
            assert field.path not in seen_obs and field.path not in seen_int
        elif field.klass is ryo_fields.FieldClass.OBSERVATION:
            assert field.path in seen_obs and field.path not in seen_int, field.path
        else:
            assert field.path in seen_int and field.path not in seen_obs, field.path
    for item in (result.observation, result.interpretation):
        assert item.status == envelope.status and item.data_mode == envelope.data_mode and item.tool == tool
        for path, value in item.fields:
            assert isinstance(value, str | bool), path
        record = item.to_record("urn:hoshitori:evidence:test", item.known_at_ms)
        assert evidence.from_record(canonical.parse_record(canonical.dumps(record))) == item
    store = lineage.EvidenceStore()
    for item in (result.observation, result.interpretation):
        store.add(item, evidence.record_digest(item.to_record("urn:x", item.known_at_ms)))
    both = store.independence([result.observation.evidence_id, result.interpretation.evidence_id])
    assert (both.count, both.calls, both.origins) == (1, 2, (evidence.RYO,)), "two items of one answer are one origin"


def test_no_number_of_a_recording_became_a_float() -> None:
    result, _, _, _ = built("analyze_token")
    for path, value in result.observation.fields:
        if "price" in path or "rsi" in path or "pct" in path:
            assert isinstance(value, str) and "e" not in value.lower(), (path, value)
            json.loads(value)  # a plain decimal


def test_a_degraded_recording_keeps_no_zero_it_cannot_trust() -> None:
    """deep_analysis was recorded with its token profile partial: every zero in it is unknown, whatever field it is on."""
    result, walked, envelope, _ = built("deep_analysis")
    assert any(word == "partial" for word in envelope.availability.values()), "the recording is no longer a degraded one"
    zeros = [f.path for f in walked.fields if f.kind == "number" and f.value is not None and str(f.value) in ("0", "0.0")]
    kept = {p for p, v in result.observation.fields}
    for path in zeros:
        assert path not in kept, f"{path} is a zero in a degraded answer and was kept"
