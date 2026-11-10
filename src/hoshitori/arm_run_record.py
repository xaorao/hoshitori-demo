"""What a model arm did for one slot, as a ledger record (``LEAGUE_EXPERIMENT`` sections 3 to 5).

The forecast says what an arm committed to; the model-call records say what was asked and what came back. This record says how the
slot ended and the measures that make the arms comparable: when the arm finished against its cutoff, how many calls and billed tokens
it took, the probability the model proposed before the gates beside the one it finally gave, and, for the debate and the
matched-compute control, the facts the reasoning-value report reads: whether the debate collapsed, who cited what, how many samples
the control drew against what it was matched to, and whether the match missed by more than the tolerance.

One record per slot, identified by its forecast id, so a record written twice is one record. A slot that failed late or failed
before any model was asked still has its record: an arm that did not answer is a measured fact, never a gap.
"""

from __future__ import annotations

import re
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "arm_run"
TYPE_VERSION: Final = 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
MAX_COUNT: Final = 10**12
ARMS: Final = ("L1", "L1x", "L2")
VERDICTS: Final = ("CALL", "NO_CALL", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE")
GATES: Final = ("NONE", "GROUNDING")
ROLES: Final = ("bull", "bear")
_MEMBERS: Final = frozenset(
    {
        "cycle_id", "forecast_id", "arm", "candidate_id", "hours", "verdict", "reason", "gate", "proposed_p", "final_p", "version",
        "cutoff_ms", "finished_at_ms", "calls", "tokens", "debate", "control",
    }
)  # fmt: skip
_DEBATE_MEMBERS: Final = frozenset({"collapsed", "failed_role", "disagreement"})
_DISAGREEMENT_MEMBERS: Final = ("bull_only", "bear_only", "shared", "judge_on_bull", "judge_on_bear")
_CONTROL_MEMBERS: Final = frozenset({"target_tokens", "samples", "mismatch", "collapsed"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EVIDENCE = re.compile(r"ev[a-z2-7]{26}")
_DECIMAL = re.compile(r"0(?:\.[0-9]{1,6})?|1(?:\.0{1,6})?")


class ArmRunRecordError(ValueError):
    """A run that cannot be recorded, or a record that is not one."""


def _int(value: object, what: str, low: int = 0, high: int = MAX_COUNT) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ArmRunRecordError(f"{what} is a whole number from {low} to {high}")


def _text(value: object, what: str, pattern: re.Pattern[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ArmRunRecordError(f"{what} is not in the form this record takes")


def _flag(value: object, what: str) -> None:
    if not isinstance(value, bool):
        raise ArmRunRecordError(f"{what} is true or false")


def _ids(value: object, what: str) -> None:
    if not isinstance(value, list) or value != sorted(set(cast(list[str], value))):
        raise ArmRunRecordError(f"{what} is a sorted list of evidence ids, once each")
    for item in value:
        _text(item, what, _EVIDENCE)


def check_debate(debate: dict[str, canonical.JsonValue] | None) -> None:
    if debate is None:
        return
    if set(debate) != _DEBATE_MEMBERS:
        raise ArmRunRecordError(f"a debate note has the members {sorted(_DEBATE_MEMBERS)}")
    _flag(debate["collapsed"], "collapsed")
    if debate["failed_role"] is not None and debate["failed_role"] not in ROLES:
        raise ArmRunRecordError(f"the failed role is one of {ROLES}")
    disagreement = debate["disagreement"]
    if disagreement is None:
        return
    if not isinstance(disagreement, dict) or set(disagreement) != set(_DISAGREEMENT_MEMBERS):
        raise ArmRunRecordError(f"a disagreement has the members {sorted(_DISAGREEMENT_MEMBERS)}")
    for name in _DISAGREEMENT_MEMBERS:
        _ids(disagreement[name], name)
    if debate["failed_role"] is not None:
        raise ArmRunRecordError("a debate that lost a side has no disagreement to record")


def check_control(control: dict[str, canonical.JsonValue] | None) -> None:
    if control is None:
        return
    if set(control) != _CONTROL_MEMBERS:
        raise ArmRunRecordError(f"a control note has the members {sorted(_CONTROL_MEMBERS)}")
    _int(control["target_tokens"], "the target tokens")
    _int(control["samples"], "the samples", 0, 15)
    _flag(control["mismatch"], "mismatch")
    _flag(control["collapsed"], "collapsed")


def check_body(body: dict[str, canonical.JsonValue]) -> None:
    """The body is one that can be recorded, or an error."""
    if set(body) != _MEMBERS:
        raise ArmRunRecordError(f"the body has the members {sorted(_MEMBERS)}")
    for name in ("cycle_id", "forecast_id", "candidate_id"):
        _text(body[name], name, _ID)
    if body["arm"] not in ARMS:
        raise ArmRunRecordError(f"the arm is one of {ARMS}")
    _int(body["hours"], "the horizon in hours", 1, 24 * 366)
    if body["verdict"] not in VERDICTS:
        raise ArmRunRecordError(f"the verdict is one of {VERDICTS}")
    _text(body["reason"], "the reason", _REASON, optional=True)
    if body["gate"] not in GATES:
        raise ArmRunRecordError(f"the gate is one of {GATES}")
    _text(body["proposed_p"], "the proposed probability", _DECIMAL, optional=True)
    _text(body["final_p"], "the final probability", _DECIMAL, optional=True)
    if (body["verdict"] == "CALL") != (body["final_p"] is not None):
        raise ArmRunRecordError("a call has a probability and nothing else has")
    if (body["verdict"] == "CALL") == (body["reason"] is not None):
        raise ArmRunRecordError("a call has no reason and everything else has one")
    _text(body["version"], "the agent version", _SHA256)
    _int(body["cutoff_ms"], "the cutoff", 1, 86_400_000)
    _int(body["finished_at_ms"], "the finishing time", 0, MAX_INSTANT_MS)
    _int(body["calls"], "the number of calls")
    _int(body["tokens"], "the billed tokens")
    debate, control = body["debate"], body["control"]
    if (debate is not None and not isinstance(debate, dict)) or (control is not None and not isinstance(control, dict)):
        raise ArmRunRecordError("a note is an object")
    if (debate is not None) != (body["arm"] == "L2") or (control is not None) != (body["arm"] == "L1x"):
        raise ArmRunRecordError("the debate has a debate note, the control a control note, and the single judgment neither")
    check_debate(debate)
    check_control(control)


def record_id(forecast_id: str) -> str:
    return natural_id("ar", {"forecast": forecast_id})


def to_record(body: dict[str, canonical.JsonValue], source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger."""
    check_body(body)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise ArmRunRecordError("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": record_id(cast(str, body["forecast_id"])),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": body,
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise ArmRunRecordError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> dict[str, canonical.JsonValue]:
    """The body a record holds, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise ArmRunRecordError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise ArmRunRecordError("not an arm run record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    check_body(body)
    if record["id"] != record_id(cast(str, body["forecast_id"])):
        raise ArmRunRecordError("the id is not the id of what the record says")
    return body
