"""A RYO answer turned into evidence: one observation item and, where the answer has any, one separate interpretation item.

The rules, each pinned by ``tests/ryo_evidence_checks.py``:

* **Two items, never one.** The fields the evidence-class table calls observations go in an OBSERVATION item; the ones it calls
  interpretations (verdicts, trend labels, prose) go in an INTERPRETATION item of the same capture. An independent arm is given the first
  and never the second (``snapshot.freeze`` and ``arm_input`` enforce that; this module only keeps the two apart).
* **A null is unknown, a degraded zero is unknown.** A null is ``NULL``. A numeric zero in an answer that was not wholly available (its
  status is not ``ok``, or a section's availability word is neither ``available`` nor ``not_requested``) is ``DEGRADED_ZERO``: it is not a
  measurement. The same zero in a wholly available answer is a zero.
* **Numbers are spelled as RYO spelled them**, as plain decimal text; nothing passes through a binary float.
* **Nothing enters that is not live, not available, or about another token** than the one asked for. Those answers raise
  ``RyoEvidenceError``; an answer that does not fit the contract raises the connector's own ``ConnectorError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from hoshitori import canonical
from hoshitori.capture import RawCapture
from hoshitori.evidence import RYO, EvidenceError, EvidenceItem, FieldValue, Kind, Redistribution, Unknown
from hoshitori.evidence_sources import pattern_of
from hoshitori.ryo_envelope import Envelope, read_rest_success
from hoshitori.ryo_fields import OBS, classification_table, walk
from hoshitori.ryo_fields import FieldValue as WalkedField
from hoshitori.timeline import to_ms

WHOLE_WORDS: Final = frozenset({"available", "not_requested"})
ASSET_TOOLS: Final = frozenset({"analyze_token", "deep_analysis"})


class RyoEvidenceError(ValueError):
    """An answer that must not become evidence."""


@dataclass(frozen=True)
class Made:
    observation: EvidenceItem
    interpretation: EvidenceItem | None


def is_whole(envelope: Envelope) -> bool:
    """Whether every section of the answer was available (an unavailable or partial section makes a zero in it untrustworthy)."""
    return envelope.status == "ok" and all(word in WHOLE_WORDS for word in envelope.availability.values())


def _is_zero(field: WalkedField) -> bool:
    if field.kind != "number" or not isinstance(field.value, str):
        return False
    try:
        return Decimal(field.value) == 0
    except InvalidOperation:
        return False


def _check_symbols(envelope: Envelope, request: dict[str, canonical.JsonValue]) -> None:
    """A comparison is about the tokens that were asked for, no more and no fewer."""
    asked = request.get("symbols")
    tokens = envelope.data.get("tokens")
    if not isinstance(asked, str) or not isinstance(tokens, list):
        raise RyoEvidenceError("the comparison cannot be checked against the tokens it was asked about")
    wanted = {part.strip().upper() for part in asked.replace(",", " ").split() if part.strip()}
    told = {str(t.get("symbol", "")).strip().upper() for t in tokens if isinstance(t, dict)}
    if told != wanted:
        raise RyoEvidenceError(f"RYO compared {sorted(told)}, not {sorted(wanted)}")


def _check_symbol(envelope: Envelope, tool: str, request: dict[str, canonical.JsonValue]) -> None:
    if tool == "compare_tokens":
        _check_symbols(envelope, request)
        return
    asked = request.get("symbol")
    if tool not in ASSET_TOOLS or not isinstance(asked, str):
        return
    asset = envelope.data.get("asset")
    told = asset.get("symbol") if isinstance(asset, dict) else None
    if not isinstance(told, str) or told.strip().upper() != asked.strip().upper():
        raise RyoEvidenceError(f"RYO answered about {told}, not {asked}")


def items_from(capture: RawCapture, tool: str, request: dict[str, canonical.JsonValue], adapter_version: str) -> Made:
    """The evidence items of the answer held by ``capture`` to a call of ``tool`` with ``request``."""
    if capture.request.get("mode") == "recorded":  # the body of a replay still says live; the capture says what it was
        raise RyoEvidenceError("a recorded answer is a replay: nothing that is not a live read enters a cycle")
    envelope = read_rest_success(capture.body, tool)
    if envelope.status == "unavailable":
        raise RyoEvidenceError("RYO says the answer is unavailable")
    if envelope.data_mode != "live":
        raise RyoEvidenceError(f"RYO's data mode is {envelope.data_mode!r}, not 'live': nothing that is not live enters a cycle")
    _check_symbol(envelope, tool, request)
    walked = walk(tool, envelope.data)
    whole = is_whole(envelope)
    classes = {row[0]: row[1] for row in classification_table(tool)}
    known: dict[str, dict[str, FieldValue]] = {"OBSERVATION": {}, "INTERPRETATION": {}}
    unknown: dict[str, dict[str, Unknown]] = {"OBSERVATION": {}, "INTERPRETATION": {}}
    for item in walked.fields:
        side = "OBSERVATION" if item.klass is OBS else "INTERPRETATION" if item.klass.value == "INTERPRETATION" else None
        if side is None:
            continue
        if item.value is None:
            unknown[side][item.path] = Unknown.NULL
        elif side == "OBSERVATION" and not whole and _is_zero(item):
            unknown[side][item.path] = Unknown.DEGRADED_ZERO
        else:
            known[side][item.path] = item.value
    for path in walked.missing:
        side = classes.get(pattern_of(path))
        if side in known and path not in known[side] and path not in unknown[side]:
            unknown[side][path] = Unknown.ABSENT
    received = to_ms(capture.received_at)
    common = {
        "adapter_version": adapter_version,
        "kind": Kind.OBSERVATION,
        "source": capture.source,
        "origin": RYO,
        "received_at_ms": received,
        "known_at_ms": received,
        "capture_id": capture.capture_id,
        "raw_sha256": capture.body_sha256,
        "tool": tool,
        "request": tuple(sorted(request.items())),
        "status": envelope.status,
        "data_mode": envelope.data_mode,
        "availability": tuple(sorted(envelope.availability.items())),
        "warnings": envelope.warnings,
        "event_time_ms": to_ms(envelope.as_of),
        "redistribution": Redistribution.PRIVATE_ONLY,  # until the organisers say whether RYO responses may be shown
    }

    def build(side: str, kind: Kind, path: str) -> EvidenceItem | None:
        if not known[side] and not unknown[side]:
            return None
        return EvidenceItem(
            **{**common, "kind": kind, "path": path},  # type: ignore[arg-type]
            fields=tuple(sorted(known[side].items())),
            unknown_fields=tuple(sorted(unknown[side].items())),
        )

    try:
        observation = build("OBSERVATION", Kind.OBSERVATION, f"ryo.{tool}")
        interpretation = build("INTERPRETATION", Kind.INTERPRETATION, f"ryo.{tool}.interpretation")
    except EvidenceError as exc:
        raise RyoEvidenceError(f"the answer cannot be recorded as evidence: {exc}") from exc
    if observation is None:
        raise RyoEvidenceError("the answer holds no observation")
    return Made(observation, interpretation)
