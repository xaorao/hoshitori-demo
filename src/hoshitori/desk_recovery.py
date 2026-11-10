"""Restart and replay of Desk runs.

A process that dies leaves a run open. On restart every open run is closed as failed and never resumed, so no check, call or record is
repeated; a run whose forecast is already in the ledger is closed as committed instead. A replay runs the same steps again on the stored
answers (the caller supplies ports that serve them) and must give the same records; the times are not compared, and a different version of
the code is refused, because the same answers would not mean the same.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from hoshitori import canonical, desk, desk_plan, desk_record
from hoshitori.desk_menu import Menu

Record = tuple[str, dict[str, canonical.JsonValue]]
TERMINAL: Final = ("COMMITTED", "ABSTAINED", "FAILED", "FAILED_FINAL")
CLOSING_STATE: Final = "FAILED_FINAL"
CLOSING_REASON: Final = "RUN_INTERRUPTED"


class RecoveryError(ValueError):
    """Records that cannot be recovered from or replayed against."""


def _states(records: Sequence[Record]) -> dict[str, list[dict[str, canonical.JsonValue]]]:
    """The run states of every run, in the order they were written."""
    runs: dict[str, list[dict[str, canonical.JsonValue]]] = {}
    for kind, body in records:
        if kind != "desk_run_state":
            continue
        run_id = body.get("run_id")
        if not isinstance(run_id, str):
            raise RecoveryError("a run state names its run (run_id)")
        if body.get("state") not in desk_record.RUN_STATES:
            raise RecoveryError(f"the state of a run is one of {desk_record.RUN_STATES}")
        runs.setdefault(run_id, []).append(body)
    return runs


def open_runs(records: Sequence[Record]) -> dict[str, dict[str, canonical.JsonValue]]:
    """The runs that have no terminal state, each with the last state written for it."""
    return {run: states[-1] for run, states in _states(records).items() if not any(s["state"] in TERMINAL for s in states)}


def close_lost_runs(records: Sequence[Record], now_ms: int, committed: Mapping[str, str] | None = None) -> list[Record]:
    """The records that close every open run, in the order of the run ids. ``committed`` maps a run to its forecast where the ledger already
    holds it; such a run is closed as committed and every other as failed."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise RecoveryError("now_ms is a whole number of milliseconds")
    closing: list[Record] = []
    for run, last in sorted(open_runs(records).items()):
        body = dict(last)
        body["at_ms"] = now_ms
        if run in (committed or {}):
            body.update({"state": "COMMITTED", "reason": None, "forecast_id": (committed or {})[run]})
        else:
            body.update({"state": CLOSING_STATE, "reason": CLOSING_REASON, "forecast_id": None})
        desk_record.check_body("desk_run_state", body)
        closing.append(("desk_run_state", body))
    return closing


@dataclass(frozen=True)
class Replayed:
    equal: bool
    differences: tuple[str, ...]
    produced: tuple[Record, ...]


def _same(body: dict[str, canonical.JsonValue]) -> dict[str, canonical.JsonValue]:
    return {k: v for k, v in body.items() if k != "at_ms"}


def replay_run(records: Sequence[Record], trigger: desk_plan.Trigger, menu: Menu, ports: desk.Ports, code_version: str) -> Replayed:
    """Run the steps of a stored run again on the stored answers and say whether the records are the same."""
    first = next((body for kind, body in records if kind == "desk_run_state"), None)
    if first is None or first["state"] != "RUNNING":
        raise RecoveryError("the records have no first run state, RUNNING, to start a replay from")
    if first["code_version"] != code_version:
        raise RecoveryError("the run was made by another version of the code")
    if first["menu_sha256"] != menu.sha256:
        raise RecoveryError("the menu is not the menu of the run")
    if (first["symbol"], first["discovery_route"], first["trigger_id"]) != (trigger.symbol, trigger.route, trigger.trigger_id):
        raise RecoveryError("the trigger is not the trigger of the run")
    planned = desk_plan.hypotheses(trigger)[0]
    for kind, body in records:
        if kind == "hypothesis" and body["hypothesis_id"] != planned.hypothesis_id:
            raise RecoveryError("the trigger does not give the hypothesis of the run")
    budget = cast(dict[str, canonical.JsonValue], first["budget"])
    calls = cast(dict[str, int], budget["calls"])
    versions = desk.Versions(cast(str, first["agent_version_hash"]), code_version, cast(str, first["protocol_hash"]))
    quiet = dataclasses.replace(ports, sink=lambda kind, body: None)
    outcome = desk.run(
        quiet,
        trigger,
        menu,
        desk.Budget(calls, cast(int, budget["seconds"]), cast(int, budget["tokens"])),
        cast(str, first["run_id"]),
        desk.Binding(cast(str, first["label"])),
        versions,
    )
    differences: list[str] = []
    produced = outcome.records
    if len(produced) != len(records):
        differences.append(f"records: {len(produced)} produced, {len(records)} stored")
    for position, ((kind, made), (stored_kind, stored)) in enumerate(zip(produced, records, strict=False)):
        if kind != stored_kind:
            differences.append(f"{stored_kind}#{position}: kind")
            continue
        left, right = _same(made), _same(stored)
        differences.extend(f"{kind}#{position}: {name}" for name in sorted(set(left) | set(right)) if left.get(name) != right.get(name))
    return Replayed(not differences, tuple(differences), produced)
