"""The Desk's decision log as ledger records (contract section 57): run state, hypothesis, check, falsification and decision.

Every record is public: it holds ids, digests, closed words, counts and the product's own template text, and never a value or a sentence
that came from RYO or any source (those stay in the evidence records). Each kind has a writer and a strict reader: the reader refuses any
member it does not know, any value outside the form its writer produces, any rule that ties members together broken, and any id that is
not the natural id of what the record says. A record delivered twice is one record.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final, cast

from hoshitori import canonical, desk_acquire, desk_gates, desk_menu, envelope, falsify, lifecycle
from hoshitori.claim_graph import EdgeKind, NodeType
from hoshitori.ids import natural_id
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPES: Final = ("desk_run_state", "hypothesis", "desk_check", "falsification", "decision")
TYPE_VERSION: Final = 1
PREFIXES: Final = {"desk_run_state": "dr", "hypothesis": "dh", "desk_check": "dc", "falsification": "df", "decision": "dd"}
RUN_STATES: Final = ("RUNNING", "STOPPED", "COMMITTED", "ABSTAINED", "FAILED", "FAILED_FINAL")
STOP_REASONS: Final = ("UTILITY_BELOW_THRESHOLD", "CANNOT_FLIP_DECISION", "BUDGET_EXHAUSTED", "ALL_MENU_OPENED")
ABSTAIN_REASONS: Final = ("BELOW_MINIMUM_EDGE", "ESSENTIAL_PREMISE_UNPROVEN", "EVIDENCE_CONFLICT_UNRESOLVED", "OUT_OF_SCOPE")
FAIL_REASONS: Final = ("FAILED", "FAILED_EVIDENCE", "FAILED_STALE", "MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE", "RUN_INTERRUPTED")
ROUTES: Final = ("RYO_SCAN", "EVENT", "WATCHLIST")
LABELS: Final = ("live", "recorded", "synthetic")  # synthetic: invented answers written for an example, read through the recorded path
LAYERS: Final = ("NONE", "DETERMINISTIC", "MOVE_CHECK", "PERSON")
MAX_INSTANT_MS: Final = 253_402_300_799_999
MAX_COUNT: Final = 10**12
MAX_NODES: Final = 8
MAX_SPAN: Final = 300  # the longest a statement is
MAX_SEQ: Final = 10**6
FAILED_GATE_REASONS: Final = ("FAILED_EVIDENCE", "FAILED_STALE")
assert set(ABSTAIN_REASONS) == lifecycle.NO_CALL_REASONS  # the abstentions are the lifecycle's own words
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_FAMILY = re.compile(r"RYO|(EXCHANGE|CHAIN|PRIMARY_DOC):[A-Za-z0-9@._-]{1,64}")
_WORD = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_EVIDENCE = re.compile(r"ev[a-z2-7]{26}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}")
_TEXT = re.compile(r"[A-Za-z0-9 .,:;()'%/_+=-]{1,300}")
_NOTE = re.compile(r"[A-Za-z0-9 .,:;()'%/_+=-]{0,300}")
_SHORT = re.compile(r"[A-Za-z0-9._:/-]{0,100}")
_DECIMAL = re.compile(r"0(?:\.[0-9]{1,6})?|1(?:\.0{1,6})?")
_SECONDS = re.compile(r"[0-9]{1,9}\.[0-9]{3}")
_STAT = re.compile(r"[0-9A-Za-z._+-]{0,40}")
_KEY = re.compile(r"[a-z][a-z_0-9]{0,39}")
_UNRESOLVED = re.compile(r"[A-Za-z0-9_]+:[A-Za-z_]+")
_CHECK_ID = re.compile(r"[A-Z_]+:[A-Za-z0-9._-]+:[A-Za-z0-9._-]+")


class DeskRecordError(ValueError):
    """A record that cannot be written, or is not one."""


def _fail(message: str) -> DeskRecordError:
    return DeskRecordError(message)


# Each helper reads ``obj[key]`` and names the member by its key in a refusal, so a message can never name the wrong member.
def _is_int(value: object, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _fail(f"{label} is a whole number from {low} to {high}")
    return value


def _int(obj: dict[str, canonical.JsonValue], key: str, low: int = 0, high: int = MAX_COUNT) -> int:
    return _is_int(obj[key], key, low, high)


def _is_text(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _fail(f"{label} is not in the form this record takes")
    return value


def _text(obj: dict[str, canonical.JsonValue], key: str, pattern: re.Pattern[str]) -> str:
    return _is_text(obj[key], key, pattern)


def _opt_text(obj: dict[str, canonical.JsonValue], key: str, pattern: re.Pattern[str]) -> None:
    if obj[key] is not None:
        _is_text(obj[key], key, pattern)


def _word(obj: dict[str, canonical.JsonValue], key: str, allowed: tuple[str, ...]) -> str:
    value = obj[key]
    if not isinstance(value, str) or value not in allowed:
        raise _fail(f"{key} is one of {allowed}")
    return value


def _opt_word(obj: dict[str, canonical.JsonValue], key: str, allowed: tuple[str, ...]) -> None:
    if obj[key] is not None:
        _word(obj, key, allowed)


def _flag(obj: dict[str, canonical.JsonValue], key: str) -> bool:
    value = obj[key]
    if not isinstance(value, bool):
        raise _fail(f"{key} is true or false")
    return value


def _object(obj: dict[str, canonical.JsonValue], key: str, members: tuple[str, ...]) -> dict[str, canonical.JsonValue]:
    value = obj[key]
    if not isinstance(value, dict) or set(value) != set(members):
        raise _fail(f"{key} has the members {sorted(members)}")
    return value


def _list(obj: dict[str, canonical.JsonValue], key: str) -> list[canonical.JsonValue]:
    value = obj[key]
    if not isinstance(value, list):
        raise _fail(f"{key} is a list")
    return value


def _each_object(obj: dict[str, canonical.JsonValue], key: str, members: tuple[str, ...]) -> list[dict[str, canonical.JsonValue]]:
    out = []
    for item in _list(obj, key):
        if not isinstance(item, dict) or set(item) != set(members):
            raise _fail(f"each of {key} has the members {sorted(members)}")
        out.append(item)
    return out


def _each_text(obj: dict[str, canonical.JsonValue], key: str, pattern: re.Pattern[str]) -> list[str]:
    return [_is_text(item, key, pattern) for item in _list(obj, key)]


def _sorted_ids(obj: dict[str, canonical.JsonValue], key: str, pattern: re.Pattern[str]) -> list[str]:
    items = _each_text(obj, key, pattern)
    if items != sorted(set(items)):
        raise _fail(f"{key} is a sorted list, once each")
    return items


def _decimal(obj: dict[str, canonical.JsonValue], key: str) -> float:
    return float(_text(obj, key, _DECIMAL))


def _opt_seconds(obj: dict[str, canonical.JsonValue], key: str) -> None:
    if obj[key] is not None:
        _is_text(obj[key], f"{key} seconds (a decimal with three places)", _SECONDS)


def _counts(obj: dict[str, canonical.JsonValue], key: str) -> None:
    body = _object(obj, key, ("calls", "seconds", "tokens"))
    calls = body["calls"]
    if not isinstance(calls, dict):
        raise _fail(f"{key} calls are a count for each provider")
    for provider, count in calls.items():
        _is_text(provider, key, _ID)
        _is_int(count, key, 0, MAX_COUNT)
    _int(body, "seconds")
    _int(body, "tokens")


def _check_run_state(body: dict[str, canonical.JsonValue]) -> None:
    _text(body, "run_id", _ID)
    state = _word(body, "state", RUN_STATES)
    allowed: dict[str, tuple[str, ...]] = {
        "STOPPED": STOP_REASONS,
        "ABSTAINED": ABSTAIN_REASONS,
        "FAILED": FAIL_REASONS,
        "FAILED_FINAL": FAIL_REASONS,
    }
    if state in allowed:
        _word(body, "reason", allowed[state])
    elif body["reason"] is not None:
        raise _fail("a run that is running or committed has no reason")
    _int(body, "at_ms", 0, MAX_INSTANT_MS)
    _word(body, "regime", ("OPERATIONAL",))
    _word(body, "label", LABELS)
    _text(body, "asset_id", _ID)
    _text(body, "symbol", _SYMBOL)
    _word(body, "discovery_route", ROUTES)
    _opt_text(body, "trigger_id", _ID)
    _word(body, "policy", ("P-DEPENDENCY",))
    _word(body, "calibration", ("UNCALIBRATED",))
    _text(body, "menu_sha256", _SHA256)
    _counts(body, "budget")
    _counts(body, "spent")
    for name in ("agent_version_hash", "code_version", "protocol_hash"):
        _text(body, name, _SHA256)
    _opt_text(body, "forecast_id", _ID)
    if (state == "COMMITTED") != (body["forecast_id"] is not None):
        raise _fail("a committed run names its forecast and no other run does")


def _check_hypothesis(body: dict[str, canonical.JsonValue]) -> None:
    _text(body, "run_id", _ID)
    _text(body, "hypothesis_id", _ID)
    _text(body, "template_id", _WORD)
    statement = _text(body, "statement", _TEXT)
    _text(body, "invalidation", _TEXT)
    graph = _object(body, "graph", ("nodes", "edges"))
    nodes = _each_object(graph, "nodes", ("node_id", "type", "span"))
    if not 1 <= len(nodes) <= MAX_NODES:
        raise _fail(f"a graph has from 1 to {MAX_NODES} nodes")
    seen: set[str] = set()
    for node in nodes:
        ident = _text(node, "node_id", _ID)
        if ident in seen:
            raise _fail("a node id appears once")
        seen.add(ident)
        _word(node, "type", tuple(t.value for t in NodeType))
        key = "span"
        span = _list(node, key)
        if len(span) != 2:
            raise _fail("a span is two offsets into the statement")
        start, end = _is_int(span[0], key, 0, MAX_SPAN), _is_int(span[1], key, 1, MAX_SPAN)
        if not start < end <= len(statement):
            raise _fail("a span is two offsets into the statement, the first before the second")
    for edge in _each_object(graph, "edges", ("from", "to", "kind")):
        if _text(edge, "from", _ID) not in seen or _text(edge, "to", _ID) not in seen:
            raise _fail("an edge joins two nodes of the graph")
        _word(edge, "kind", tuple(k.value for k in EdgeKind))
    _word(body, "stance", desk_gates.PRE_STANCES)
    _word(body, "discovery_route", ROUTES)
    _int(body, "triage_rule_version", 1, 1000)


def _check_desk_check(body: dict[str, canonical.JsonValue]) -> None:
    _text(body, "run_id", _ID)
    _int(body, "seq", 0, MAX_SEQ)
    _text(body, "node_id", _ID)
    _word(body, "node_type", tuple(t.value for t in NodeType))
    _text(body, "check_id", _CHECK_ID)
    _word(body, "check_type", desk_menu.CHECK_TYPES)
    _text(body, "provider", _ID)
    _text(body, "operation", _ID)
    _text(body, "origin_family", _FAMILY)
    pairs = []
    key = "args"
    for pair in _list(body, key):
        if not isinstance(pair, list) or len(pair) != 2:
            raise _fail("an argument is a name and a value")
        pairs.append((_is_text(pair[0], key, _KEY), _is_text(pair[1], key, _SHORT)))
    if pairs != sorted(pairs):
        raise _fail("the args are sorted by name")
    _word(body, "chosen_by", ("DEPENDENCY_ORDER", "ADDRESS_SCREEN"))
    why = _object(body, "why", ("template_id", "settled"))
    _text(why, "template_id", _WORD)
    _sorted_ids(why, "settled", _ID)
    for alternative in _each_object(body, "alternatives", ("check_id", "rule")):
        _text(alternative, "check_id", _CHECK_ID)
        _text(alternative, "rule", _WORD)
    expected = _object(body, "cost_expected", ("calls", "seconds"))
    _int(expected, "calls")
    _opt_seconds(expected, "seconds")
    spent = _object(body, "cost_spent", ("calls", "attempts", "seconds"))
    _int(spent, "calls")
    _int(spent, "attempts")
    _opt_seconds(spent, "seconds")
    outcome = _word(body, "outcome", desk_acquire.OUTCOMES)
    _opt_word(body, "error_class", desk_acquire.ERROR_CLASSES)
    if (outcome == "OK") != (body["error_class"] is None):
        raise _fail("a check that worked has no error class and every other has one")
    _each_text(body, "capture_ids", _ID)
    if _sorted_ids(body, "evidence_ids", _EVIDENCE) and outcome != "OK":
        raise _fail("a check that did not work brought no evidence")
    _text(body, "status_before", _WORD)
    _text(body, "own_status", _WORD)
    _text(body, "status_after", _WORD)
    _word(body, "layer", LAYERS)


def _check_test(test: dict[str, canonical.JsonValue]) -> None:
    kind = _word(test, "type", falsify.TESTS)
    _text(test, "method", _SHORT)
    _text(test, "version", _SHORT)
    for name in ("parameters", "statistics"):
        values = test[name]
        if not isinstance(values, dict):
            raise _fail(f"{name} are a map of names to text")
        for key, value in values.items():
            _is_text(key, f"a key of {name}", _KEY)
            _is_text(value, f"a value of {name}", _STAT)
    result = _word(test, "result", falsify.LABELS)
    if result == "NOT_TESTABLE":
        _word(test, "reason", falsify.REASONS)
    elif test["reason"] is not None and not (result == "INCONCLUSIVE" and test["reason"] == "uncalibrated"):
        raise _fail("only a test that cannot be run, or one that was not calibrated, has a reason")
    calibrated = _flag(test, "calibrated")
    _opt_text(test, "calibration_sha256", _SHA256)
    if calibrated and kind != "T_SOURCE" and test["calibration_sha256"] is None:
        raise _fail("a calibrated test names its calibration")
    if not calibrated and test["calibration_sha256"] is not None:
        raise _fail("a test that was not calibrated names no calibration")


def _check_falsification(body: dict[str, canonical.JsonValue]) -> None:
    _text(body, "run_id", _ID)
    _text(body, "hypothesis_id", _ID)
    _text(body, "target_assumption", _ID)
    _text(body, "proposed_explanation", _WORD)
    for note in _each_object(body, "competing_explanations", ("klass", "predicts")):
        _text(note, "klass", _WORD)
        _text(note, "predicts", _TEXT)
    for test in _each_object(
        body,
        "tests",
        ("type", "method", "version", "parameters", "statistics", "result", "reason", "calibrated", "calibration_sha256"),
    ):
        _check_test(test)
    _each_text(body, "unresolved", _UNRESOLVED)
    completed = _flag(body, "completed")
    _opt_word(body, "label", ("NOT_FALSIFIED_ATTEMPTED",))
    if completed != (body["label"] is None):
        raise _fail("a step that was not completed carries the label and one that was completed has none")
    target = _object(body, "pre_test_target", ("stance", "p", "weight"))
    _word(target, "stance", desk_gates.PRE_STANCES)
    target_p, target_weight = _decimal(target, "p"), _decimal(target, "weight")
    if target["stance"] == "FLAT" and target_weight != 0:
        raise _fail("a flat target has no weight")
    outcome = _word(body, "outcome", ("PASSED", "FAILED"))
    ceiling = _object(body, "ceiling", ("weight", "p_cap"))
    cap_weight, cap_p = _decimal(ceiling, "weight"), _decimal(ceiling, "p_cap")
    if cap_weight > target_weight or cap_p > target_p:
        raise _fail("the ceiling is never above the pre-test target")
    if outcome == "PASSED":
        if not completed:
            raise _fail("a step that was not completed did not pass")
        if (cap_weight, cap_p) != (target_weight, target_p):
            raise _fail("after a passed step the ceiling is the pre-test target")
    elif (target_weight > 0 and cap_weight >= target_weight) or (target_p > 0.5 and cap_p >= target_p):
        raise _fail("after a failed step the ceiling is below the pre-test target")


def _check_decision(body: dict[str, canonical.JsonValue]) -> None:
    for name in ("decision_id", "run_id", "candidate_id"):
        _text(body, name, _ID)
    _word(body, "arm_id", ("DESK",))
    proposal = _object(body, "proposal", ("stance", "p", "weight", "cites", "no_call_reason"))
    _word(proposal, "stance", desk_gates.STANCES)
    _decimal(proposal, "p")
    _decimal(proposal, "weight")
    cites = _sorted_ids(proposal, "cites", _EVIDENCE)
    _opt_word(proposal, "no_call_reason", ABSTAIN_REASONS)
    evidence = _sorted_ids(body, "evidence_ids", _EVIDENCE)
    if not set(cites) <= set(evidence):
        raise _fail("the cites are among the evidence ids")
    events = _each_object(body, "gate_events", ("gate", "status", "detail"))
    if not events:
        raise _fail("a decision has gate events")
    last = -1
    for position, event in enumerate(events):
        gate = _word(event, "gate", desk_gates.GATES)
        status = _word(event, "status", desk_gates.STATUSES)
        _text(event, "detail", _NOTE)
        if desk_gates.GATES.index(gate) <= last:
            raise _fail("the gates are in their fixed order, each once")
        last = desk_gates.GATES.index(gate)
        if status == "STOP" and position != len(events) - 1:
            raise _fail("a stop is the last gate event")
    action = _object(body, "final_action", ("kind", "reason", "stance", "p", "weight"))
    kind = _word(action, "kind", desk_gates.KINDS)
    if (kind == "COMMIT") == (events[-1]["status"] == "STOP"):
        raise _fail("a commitment ends on a gate that passed and anything else on one that stopped (the last gate event)")
    if kind == "COMMIT":
        if action["reason"] is not None:
            raise _fail("a commitment has no reason")
        _word(action, "stance", desk_gates.PRE_STANCES)
        _decimal(action, "p")
        _decimal(action, "weight")
    else:
        _word(action, "reason", ABSTAIN_REASONS if kind == "ABSTAIN" else FAILED_GATE_REASONS)
        if any(action[name] is not None for name in ("stance", "p", "weight")):
            raise _fail("only a commitment has a final stance, probability and weight")
    _text(body, "observation_set_hash", _SHA256)
    _text(body, "model_outputs_digest", _SHA256)


_CHECKS: Final[dict[str, Callable[[dict[str, canonical.JsonValue]], None]]] = {
    "desk_run_state": _check_run_state,
    "hypothesis": _check_hypothesis,
    "desk_check": _check_desk_check,
    "falsification": _check_falsification,
    "decision": _check_decision,
}
_MEMBERS: Final = {
    "desk_run_state": (
        "run_id state reason at_ms regime label asset_id symbol discovery_route trigger_id policy calibration menu_sha256 budget spent "
        "agent_version_hash code_version protocol_hash forecast_id"
    ),
    "hypothesis": "run_id hypothesis_id template_id statement invalidation graph stance discovery_route triage_rule_version",
    "desk_check": (
        "run_id seq node_id node_type check_id check_type provider operation origin_family args chosen_by why alternatives cost_expected "
        "cost_spent outcome error_class capture_ids evidence_ids status_before own_status status_after layer"
    ),
    "falsification": (
        "run_id hypothesis_id target_assumption proposed_explanation competing_explanations tests unresolved completed label "
        "pre_test_target outcome ceiling"
    ),
    "decision": (
        "decision_id run_id arm_id candidate_id proposal gate_events final_action evidence_ids observation_set_hash model_outputs_digest"
    ),
}
_KEYS: Final = {
    "desk_run_state": ("run_id", "state"),
    "hypothesis": ("run_id", "hypothesis_id"),
    "desk_check": ("run_id", "seq"),
    "falsification": ("run_id", "hypothesis_id"),
    "decision": ("decision_id",),
}


def check_body(kind: str, body: object) -> None:
    """The body is one that can be recorded, or an error."""
    if kind not in TYPES:
        raise _fail(f"the kind is one of {TYPES}")
    if not isinstance(body, dict) or set(body) != set(_MEMBERS[kind].split()):
        raise _fail(f"the body of a {kind} has the members {sorted(_MEMBERS[kind].split())}")
    _CHECKS[kind](body)


def record_id(kind: str, body: dict[str, canonical.JsonValue]) -> str:
    if kind not in TYPES:
        raise _fail(f"the kind is one of {TYPES}")
    return natural_id(PREFIXES[kind], {name: body[name] for name in _KEYS[kind]})


def to_record(kind: str, body: dict[str, canonical.JsonValue], source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger."""
    check_body(kind, body)
    if isinstance(created_at_ms, bool) or not isinstance(created_at_ms, int) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise _fail("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": kind,
        "type_version": TYPE_VERSION,
        "id": record_id(kind, body),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": body,
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise _fail(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> tuple[str, dict[str, canonical.JsonValue]]:
    """The kind and body a record holds, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise _fail(f"the envelope: {exc}") from exc
    kind = record["type"]
    if kind not in TYPES or record["type_version"] != TYPE_VERSION:
        raise _fail("not a Desk record of a version this reader knows")
    if record.get("privacy") != "public":
        raise _fail("a Desk record is public")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    check_body(kind, body)
    if record["id"] != record_id(kind, body):
        raise _fail("the id is not the id of what the record says")
    return kind, body
