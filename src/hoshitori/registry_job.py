"""Refreshing the asset registry from each venue's own instrument list, through the gateway.

``CYCLE_RUNNER_SPEC`` section 2 and ``ASSET_REGISTRY_SPEC`` are the rules. One call per list (a venue and a scope); the
answer is read by the venue's adapter, admitted by the registry (which accepts it, or rejects it with a reason and keeps what
it had), and the record the registry returns is handed back for the ledger. A list that cannot be read is a failure with its
reason and the registry is untouched: the old list stays and ages, which the registry reports. Nothing is invented for a
venue that did not answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, cast

from hoshitori import canonical, envelope
from hoshitori.collect import UNREADABLE, Environment, Problem, answer, detail, why
from hoshitori.connector import ConnectorError
from hoshitori.gateway import Call
from hoshitori.governor import Priority
from hoshitori.ids import natural_id
from hoshitori.registry import SCOPES, Registry
from hoshitori.venue_data import InstrumentList

MAX_LISTS: Final = 32


class RegistryJobError(ValueError):
    """A refresh that was asked for wrongly."""


@dataclass(frozen=True)
class ListSpec:
    """One list to read: the venue (a key of the environment's adapters), the registry scope it fills and the adapter's kind argument."""

    venue: str
    scope: str
    kind: str | None = None


@dataclass(frozen=True)
class ListFailure:
    venue: str
    scope: str
    reason: str
    detail: str


@dataclass(frozen=True)
class Refresh:
    records: tuple[
        dict[str, Any], ...
    ]  # the registry's records, in the order the lists were admitted: the caller appends them to the ledger
    failures: tuple[ListFailure, ...]


def refresh(
    env: Environment, specs: Sequence[ListSpec], *, caller: str = "registry", wait_s: float = 30.0, earmark: int | None = None
) -> Refresh:
    """Read every list, admit what could be read, and say what could not."""
    if not specs or len(specs) > MAX_LISTS or len({(s.venue, s.scope) for s in specs}) != len(specs):
        raise RegistryJobError(f"a refresh reads from 1 to {MAX_LISTS} lists, each venue and scope once")
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise RegistryJobError("the wait is a positive number of seconds")
    for spec in specs:
        if spec.venue not in env.adapters:
            raise RegistryJobError(f"{spec.venue} is not a venue of this environment")
        if spec.scope not in SCOPES:
            raise RegistryJobError(f"a scope is one of {SCOPES}")
    deadline = env.clock.now() + timedelta(seconds=wait_s)
    asked = []
    failures: list[ListFailure] = []
    for spec in specs:
        adapter = env.adapters[spec.venue]
        try:
            args = adapter.normalise("instruments", {} if spec.kind is None else {"kind": spec.kind})
        except ConnectorError as exc:
            failures.append(ListFailure(spec.venue, spec.scope, "BAD_REQUEST", detail(exc.message)))
            continue
        asked.append((spec, env.runner.submit(Call(adapter.provider, "instruments", args, Priority.P0, deadline, caller, window=earmark))))
    records: list[dict[str, Any]] = []
    for spec, future in asked:
        capture = answer(env.captures, future, wait_s)
        if isinstance(capture, Problem):
            failures.append(ListFailure(spec.venue, spec.scope, capture.reason.value, capture.detail))
            continue
        try:
            listing = cast(InstrumentList, env.adapters[spec.venue].parse(capture))
            records.append(env.registry.admit(listing, scope=spec.scope, known_at=capture.received_at, capture_id=capture.capture_id))
        except UNREADABLE as exc:
            failures.append(ListFailure(spec.venue, spec.scope, "UNREADABLE", why(exc)))
    return Refresh(tuple(records), tuple(failures))


# --- the registry in the ledger ---------------------------------------------------------------------------------------------------------
TYPE: Final = "registry"
TYPE_VERSION: Final = 1


def ledger_record(record: dict[str, Any], source_urn: str) -> dict[str, canonical.JsonValue]:
    """The envelope a registry record goes into the ledger in: its id is its moment, and it is public (it is what the exchanges listed)."""
    body = cast(dict[str, canonical.JsonValue], record)
    wrapped: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": natural_id("rg", {"known_at": body["known_at"]}),
        "source": source_urn,
        "created_at": body["known_at"],
        "body": body,
        "refs": [],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(wrapped)
    except envelope.EnvelopeError as exc:
        raise RegistryJobError(f"the record does not satisfy the envelope: {exc}") from exc
    return wrapped


def load(records: Sequence[dict[str, Any]]) -> Registry:
    """The registry as the ledger holds it: every registry record, in the order the ledger has them, replayed."""
    return Registry.replay(r["body"] for r in records if r.get("type") == TYPE and r.get("type_version") == TYPE_VERSION)
