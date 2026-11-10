"""The view of Desk runs that the Desk page shows: for each run, what it was asked, what it checked and why, what it tried to disprove, how
the gates treated the proposal and how it ended.

It is built only from the Desk's own records, each checked against its schema first (``desk_record``), whose only free text is the
product's own template text, so nothing in the view can have come from a source. The words a person reads (what a check type or a gate is
called) are the page's; this view keeps the closed words of the records.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from hoshitori import canonical, desk_record
from hoshitori.desk_recovery import TERMINAL

Record = tuple[str, dict[str, canonical.JsonValue]]
FILE_FORMAT = 1


class DeskViewError(ValueError):
    """Records that cannot be shown as runs."""


@dataclass(frozen=True)
class HypothesisView:
    template_id: str
    statement: str
    invalidation: str
    stance: str


@dataclass(frozen=True)
class CheckView:
    seq: int
    node_id: str
    check_type: str
    provider: str
    origin_family: str
    operation: str
    outcome: str
    error_class: str | None
    status_before: str
    own_status: str  # what this check alone found; status_after is what the part stands at once every check so far is combined
    status_after: str
    layer: str
    alternatives: int  # how many other checks could have been chosen
    calls: int
    evidence: int


@dataclass(frozen=True)
class Origins:
    """The independent origins that read the part a run checked (contract 40): RYO is one whatever tool it was read through, an exchange is
    one whatever its products. ``read`` is every family whose check answered about the move; ``supporting`` and ``contradicting`` are those
    whose answer was a yes or a no; ``calls`` is what all the checks cost, a failed call included."""

    read: tuple[str, ...]
    supporting: tuple[str, ...]
    contradicting: tuple[str, ...]
    calls: int


@dataclass(frozen=True)
class TestView:
    type: str
    result: str
    reason: str | None
    calibrated: bool


@dataclass(frozen=True)
class FalsificationView:
    outcome: str
    completed: bool
    label: str | None
    target_assumption: str
    proposed_explanation: str
    tests: tuple[TestView, ...]
    unresolved: tuple[str, ...]
    pre_test: Mapping[str, str]
    ceiling: Mapping[str, str]


@dataclass(frozen=True)
class GateView:
    gate: str
    status: str
    detail: str


@dataclass(frozen=True)
class ProposalView:
    stance: str
    p: str
    weight: str
    cites: int
    no_call_reason: str | None


@dataclass(frozen=True)
class DecisionView:
    proposal: ProposalView
    gates: tuple[GateView, ...]
    final: Mapping[str, str | None]


@dataclass(frozen=True)
class Counts:
    calls: int
    seconds: int
    tokens: int


@dataclass(frozen=True)
class DeskRun:
    run_id: str
    label: str
    symbol: str
    route: str
    state: str
    reason: str | None
    forecast_id: str | None
    open: bool  # the run has no terminal state yet
    hypothesis: HypothesisView | None
    checks: tuple[CheckView, ...]
    origins: Origins
    falsification: FalsificationView | None
    decision: DecisionView | None
    spent: Counts
    budget: Counts
    trigger_id: str | None = None  # the trigger the run answers; none for a run that did not start from one


def _counts(body: Any) -> Counts:
    return Counts(sum(cast(Mapping[str, int], body["calls"]).values()), cast(int, body["seconds"]), cast(int, body["tokens"]))


def _once(found: dict[str, Any], kind: str, body: dict[str, Any]) -> None:
    if kind in found:
        raise DeskViewError("a run has one hypothesis, one falsification and one decision at most, each written once")
    found[kind] = body


def _group(records: Sequence[Record]) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, tuple | list) or len(item) != 2 or not isinstance(item[0], str) or not isinstance(item[1], dict):
            raise DeskViewError("a record is a kind and a body")
        kind, body = item
        if kind not in desk_record.TYPES:
            raise DeskViewError(f"the kind of a record is one of {desk_record.TYPES}")
        try:
            desk_record.check_body(kind, body)
        except desk_record.DeskRecordError as exc:
            raise DeskViewError(f"a record is refused: {exc}") from exc
        run = runs.get(cast(str, body["run_id"]))
        if kind == "desk_run_state":
            if run is None:
                if body["state"] != "RUNNING":
                    raise DeskViewError("a run begins with a RUNNING state")
                runs[cast(str, body["run_id"])] = {"states": [body], "checks": {}}
                continue
            if body["state"] == "RUNNING":
                raise DeskViewError("a run begins once, with a RUNNING state")
            if body["state"] in TERMINAL and any(s["state"] in TERMINAL for s in run["states"]):
                raise DeskViewError("a run ends once")
            run["states"].append(body)
            continue
        if run is None:
            raise DeskViewError("a record of a run that has no RUNNING state before it")
        if kind == "desk_check":
            if body["seq"] in run["checks"]:
                raise DeskViewError("each check of a run is numbered once")
            run["checks"][body["seq"]] = body
        else:
            _once(run, kind, body)
    return runs


READING: Final = ("SUPPORTED", "CONTRADICTED", "NOT_FOUND_IN_SOURCES_CHECKED")  # the answers of a source that was read about the move


def _origins(checks: Sequence[dict[str, Any]]) -> Origins:
    def families(statuses: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c["origin_family"] for c in checks if c["own_status"] in statuses))  # in the order the checks were made

    return Origins(
        families(READING),
        families(("SUPPORTED",)),
        families(("CONTRADICTED",)),
        sum(c["cost_spent"]["calls"] for c in checks),
    )


def runs(records: Sequence[Record]) -> list[DeskRun]:
    """The runs the records tell of, in the order they began."""
    out: list[DeskRun] = []
    for run_id, run in _group(records).items():
        first, last = run["states"][0], run["states"][-1]
        hypothesis, falsification, decision = run.get("hypothesis"), run.get("falsification"), run.get("decision")
        out.append(
            DeskRun(
                run_id,
                first["label"],
                first["symbol"],
                first["discovery_route"],
                last["state"],
                last["reason"],
                last["forecast_id"],
                not any(s["state"] in TERMINAL for s in run["states"]),
                None
                if hypothesis is None
                else HypothesisView(hypothesis["template_id"], hypothesis["statement"], hypothesis["invalidation"], hypothesis["stance"]),
                tuple(
                    CheckView(
                        c["seq"],
                        c["node_id"],
                        c["check_type"],
                        c["provider"],
                        c["origin_family"],
                        c["operation"],
                        c["outcome"],
                        c["error_class"],
                        c["status_before"],
                        c["own_status"],
                        c["status_after"],
                        c["layer"],
                        len(c["alternatives"]),
                        c["cost_spent"]["calls"],
                        len(c["evidence_ids"]),
                    )
                    for _, c in sorted(run["checks"].items())
                ),
                _origins(list(run["checks"].values())),
                None
                if falsification is None
                else FalsificationView(
                    falsification["outcome"],
                    falsification["completed"],
                    falsification["label"],
                    falsification["target_assumption"],
                    falsification["proposed_explanation"],
                    tuple(TestView(t["type"], t["result"], t["reason"], t["calibrated"]) for t in falsification["tests"]),
                    tuple(falsification["unresolved"]),
                    dict(falsification["pre_test_target"]),
                    dict(falsification["ceiling"]),
                ),
                None
                if decision is None
                else DecisionView(
                    ProposalView(
                        decision["proposal"]["stance"],
                        decision["proposal"]["p"],
                        decision["proposal"]["weight"],
                        len(decision["proposal"]["cites"]),
                        decision["proposal"]["no_call_reason"],
                    ),
                    tuple(GateView(g["gate"], g["status"], g["detail"]) for g in decision["gate_events"]),
                    dict(decision["final_action"]),
                ),
                _counts(last["spent"]),
                _counts(first["budget"]),
                first["trigger_id"],
            )
        )
    return out


def file_bytes(records: Sequence[Record]) -> bytes:
    """A file of Desk records as the site is given it: canonical JSON, the records in the order they were written."""
    document = {"format": FILE_FORMAT, "records": [{"kind": kind, "body": body} for kind, body in records]}
    return (json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def read_file(raw: bytes) -> list[DeskRun]:
    """The runs a file of records tells of, or the reason it cannot be read; every record is checked against its schema."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeskViewError("the file is not readable JSON") from exc
    if not isinstance(document, dict):
        raise DeskViewError("the file is one JSON object")
    if set(document) != {"format", "records"}:
        raise DeskViewError("the file has the members format and records")
    if document["format"] != FILE_FORMAT or isinstance(document["format"], bool) or not isinstance(document["format"], int):
        raise DeskViewError(f"the format of the file is {FILE_FORMAT}")
    if not isinstance(document["records"], list):
        raise DeskViewError("the records of the file are a list")
    records: list[Record] = []
    for item in document["records"]:
        if not isinstance(item, dict) or set(item) != {"kind", "body"}:
            raise DeskViewError("a record in the file is a kind and a body")
        records.append((item["kind"], item["body"]))
    return runs(records)
