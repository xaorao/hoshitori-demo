"""Envelope rules for records as a producer submits them and as the ledger holds them."""

from __future__ import annotations

import copy

import pytest

from hoshitori import envelope
from tests.mutation import mutant

GOOD = {
    "spec": "hoshitori.record/1",
    "type": "evidence_item",
    "type_version": 1,
    "id": "ev-1",
    "source": "urn:hoshitori:test",
    "created_at": "2026-10-29T11:59:00.000Z",
    "body": {"n": 1},
    "refs": [{"id": "x", "sha256": "0" * 64}],
    "privacy": "public",
}


def committed(**changes: object) -> dict[str, object]:
    record = copy.deepcopy(GOOD)
    record["committed_at"] = "2026-10-29T12:00:00.000Z"
    record.update(changes)
    return record


CASES = [
    ("committed record", committed(), True),
    ("producer record is also accepted without committed_at in the producer check", None, True),
    ("no committed_at", {k: v for k, v in committed().items() if k != "committed_at"}, False),
    ("committed_at not a string", committed(committed_at=5), False),
    ("committed_at without an offset", committed(committed_at="2026-10-29T12:00:00"), False),
    ("committed_at out of range", committed(committed_at="2026-13-29T12:00:00Z"), False),
    ("unknown member", committed(extra=1), False),
    ("wrong spec", committed(spec="other"), False),
    ("bad type", committed(type="Bad-Type"), False),
    ("type_version zero", committed(type_version=0), False),
    ("type_version true", committed(type_version=True), False),
    ("id with a space", committed(id="a b"), False),
    ("id with a trailing newline", committed(id="ev-1\n"), False),
    ("type with a trailing newline", committed(type="evidence_item\n"), False),
    ("ref hash with a trailing newline", committed(refs=[{"id": "x", "sha256": "0" * 64 + "\n"}]), False),
    ("source not a urn", committed(source="https://example.org"), False),
    ("source not ascii", committed(source="urn:é"), False),
    ("created_at naive", committed(created_at="2026-10-29T11:59:00"), False),
    ("body not an object", committed(body=[]), False),
    ("refs not a list", committed(refs="x"), False),
    ("ref with an extra member", committed(refs=[{"id": "x", "sha256": "0" * 64, "x": 1}]), False),
    ("ref hash in upper case", committed(refs=[{"id": "x", "sha256": "A" * 64}]), False),
    ("privacy value unknown", committed(privacy="secret"), False),
    ("privacy value that is a list", committed(privacy=[]), False),
    ("privacy value that is an object", committed(privacy={}), False),
]


@pytest.mark.parametrize("label,record,valid", CASES, ids=[c[0] for c in CASES])
def test_committed_record(label: str, record: dict[str, object] | None, valid: bool) -> None:
    if record is None:
        envelope.validate_producer_record(copy.deepcopy(GOOD))  # type: ignore[arg-type]
        with pytest.raises(envelope.EnvelopeError):
            envelope.validate_producer_record({**GOOD, "committed_at": "2026-10-29T12:00:00.000Z"})  # type: ignore[arg-type]
        return
    if valid:
        envelope.validate_committed_record(record)  # type: ignore[arg-type]
    else:
        with pytest.raises(envelope.EnvelopeError):
            envelope.validate_committed_record(record)  # type: ignore[arg-type]


MUTATIONS = {
    "committed_at is optional": (
        '    committed = record.get("committed_at")\n    if not isinstance(committed, str):\n        raise EnvelopeError("committed_at must be an RFC 3339 string")',
        '    committed = record.get("committed_at", "2026-10-29T12:00:00Z")',
    ),
    "committed_at need not parse": (
        '    try:\n        parse_rfc3339(committed)\n    except TimeError as exc:\n        raise EnvelopeError(f"committed_at: {exc}") from exc\n    validate_producer_record',
        "    validate_producer_record",
    ),
    "the producer envelope is not checked": (
        '    validate_producer_record({key: value for key, value in record.items() if key != "committed_at"})',
        "    pass",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.envelope", old, new)
    detected = False
    for _label, record, valid in CASES:
        if record is None:
            continue
        try:
            module.validate_committed_record(copy.deepcopy(record))  # type: ignore[arg-type]
            accepted = True
        except module.EnvelopeError:
            accepted = False
        except Exception:
            detected = True
            continue
        if accepted != valid:
            detected = True
    assert detected, f"mutant not detected: {name}"
