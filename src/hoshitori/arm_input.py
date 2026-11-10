"""What a model arm may read, and how it is shown to the model (``LEAGUE_EXPERIMENT`` sections 2 and 11; ``AGENT_PROTOCOL``
section 3).

An arm reads the frozen snapshot through a reader with independent access and nothing else: observations and what is derived
from them, never an interpretation. This module turns what that reader gives for one candidate and one horizon into the
sections of a model request:

* **One trusted section, the task**: the candidate, the horizon, the snapshot time, the event asked about and the ids that
  may be cited.
* **One untrusted section per evidence item**, always: a field of an item is text from outside (an exchange, in time a news
  feed or a vendor), so every item is shown as data in the markers of ``model_client``, whatever its source; a number in it
  is a number, an instruction in it is not an instruction.
* **Nothing is left out and nothing is made up.** The items are shown in id order, each with its path, kind, source, origin
  family, the time it became known, its status and warnings, every known field and every unknown field with its reason. An
  item that does not fit the request (more sections than a request may hold) is an error and never a silent omission. A
  candidate with a required source missing is shown as incomplete, and the arm ends that slot in ``FAILED_EVIDENCE`` without
  asking the model.
* **The reader is checked, not trusted.** A reader with any access but independent is refused, and so is any item that is an
  interpretation: the snapshot already filters them, and a second check here means a mistake in one place cannot reach a
  model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori.evidence import EvidenceItem, Kind
from hoshitori.model_client import MAX_SECTIONS, Section
from hoshitori.snapshot import Access, Candidate, Reader
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

HOUR_MS: Final = 3_600_000
RESERVED: Final = 2  # sections of a request that are not items: the task, and the debate a judge reads


class ArmInputError(ValueError):
    """An arm asked for something it may not read, or a view that cannot be shown."""


@dataclass(frozen=True)
class ArmView:
    candidate_id: str
    asset_id: str
    symbol: str
    horizon_hours: int
    snapshot_at_ms: int
    sections: tuple[Section, ...]
    evidence_ids: tuple[str, ...]  # the only ids a decision may cite
    set_hash: str  # the observation-set hash of everything shown
    missing: tuple[str, ...]  # required sources with no admitted item

    @property
    def complete(self) -> bool:
        return not self.missing


def item_text(item: EvidenceItem) -> str:
    """One item as text, every known and unknown field in path order."""
    lines = [
        f"id: {item.evidence_id}",
        f"path: {item.path}",
        f"kind: {item.kind.value}",
        f"source: {item.source}",
        f"origin: {item.origin.text}",
        f"known_at: {format_rfc3339_ms(from_ms(item.known_at_ms))}",
    ]
    if item.status is not None:
        lines.append(f"status: {item.status}")
    if item.data_mode is not None:
        lines.append(f"data_mode: {item.data_mode}")
    if item.availability:
        lines.append("availability: " + "; ".join(f"{section} = {word}" for section, word in item.availability))
    lines.extend(f"warning: {warning}" for warning in item.warnings)
    if item.fields:
        lines.append("fields:")
        lines.extend(f"  {path} = {value}" for path, value in ((p, str(v).lower() if isinstance(v, bool) else v) for p, v in item.fields))
    if item.unknown_fields:
        lines.append("unknown (not measured; never read as zero):")
        lines.extend(f"  {path} = UNKNOWN ({reason.value})" for path, reason in item.unknown_fields)
    return "\n".join(lines)


QUESTION: Final = (
    "Question: how likely is it that the close of the one-minute candle that ends one horizon after the scored origin is higher than the "
    "close of the one-minute candle that ends at the scored origin? The scored origin is the first one-minute candle close after this "
    "snapshot has been committed, a few minutes after the snapshot time. Judge only from the items below; an item that is not there was "
    "not observed. Give the ids of the items you used in the answer."
)


def task_text(symbol: str, horizon_hours: int, snapshot_at_ms: int, ids: tuple[str, ...]) -> str:
    return (
        f"Candidate: {symbol}\n"
        f"Horizon: {horizon_hours} hour(s)\n"
        f"Snapshot time: {format_rfc3339_ms(from_ms(snapshot_at_ms))}\n"
        f"{QUESTION}\n"
        "Citable ids: " + ", ".join(ids)
    )


def too_many(reader: Reader, candidate: Candidate) -> tuple[str, ...] | None:
    """The ids of a candidate's items if there are more than one request can hold (the slot then ends ``FAILED_EVIDENCE`` for every
    model arm: a data condition of this candidate, not a defect), else ``None``."""
    items = reader.items(candidate.candidate_id)
    if len(items) + RESERVED > MAX_SECTIONS:
        return tuple(item.evidence_id for item in items)
    return None


def build_view(reader: Reader, candidate: Candidate, symbol: str, horizon_ms: int, snapshot_at_ms: int) -> ArmView:
    """What an arm sees of one candidate for one horizon."""
    if reader.access is not Access.INDEPENDENT:
        raise ArmInputError("an arm reads with independent access only")
    if isinstance(horizon_ms, bool) or not isinstance(horizon_ms, int) or horizon_ms < HOUR_MS or horizon_ms % HOUR_MS:
        raise ArmInputError("a horizon is a whole number of hours")
    if not isinstance(symbol, str) or not symbol:
        raise ArmInputError("a symbol is named")
    items = reader.items(candidate.candidate_id)
    if any(item.kind is Kind.INTERPRETATION for item in items):
        raise ArmInputError("an interpretation reached an arm's reader")
    if len(items) + RESERVED > MAX_SECTIONS:
        raise ArmInputError(f"a request holds at most {MAX_SECTIONS - RESERVED} items; this candidate has {len(items)}")
    ids = tuple(item.evidence_id for item in items)
    hours = horizon_ms // HOUR_MS
    sections = (
        Section("task", task_text(symbol, hours, snapshot_at_ms, ids)),
        *(Section(f"item_{n:02d}", item_text(item), True) for n, item in enumerate(items, start=1)),
    )
    return ArmView(
        candidate.candidate_id,
        candidate.asset_id,
        symbol,
        hours,
        snapshot_at_ms,
        sections,
        ids,
        reader.set_hash(candidate.candidate_id, ids),
        reader.missing(candidate.candidate_id),
    )
