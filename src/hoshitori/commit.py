"""The batch commitment: every accepted record of a cycle goes into the ledger together, one checkpoint holds them, and independent
time-stamp tokens over that checkpoint fix when they existed.

Contract sections 2, 8, 17 and 25 are the rules. The cycle's snapshot record and the arms' records are submitted, appended in order and
covered by one signed checkpoint; its text is what the authorities stamp (``attest``). What this module adds to those two is the
bookkeeping that makes the result usable:

* **One level for the batch, judged for each record.** Every record has its own commit time and so its own counting window; the tokens are
  judged against each. The batch is ``uniform`` when every record reached the same level, which is the normal case (a batch is appended
  within a fraction of a second); if it is not, each record carries the level it reached and the cycle can decide what to do.
* **A level is only what the tokens support.** ``C3`` if two independent operators' tokens count for that record, otherwise ``C1`` (the
  ledger's own commitment). Nothing here raises a level on a promise.
* **An attested checkpoint is remembered.** On ``C3`` the ledger is told the (size, root) that an outside party has now stamped, so a
  later recovery refuses a log whose prefix differs (contract section 16).
* **The origin follows the close.** The batch's eligible time and scored origin come from the tokens that carry the level; with none, from
  the latest commit time (``Attestation.origin_ms``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, cast

from hoshitori import canonical
from hoshitori.attest import Attestation
from hoshitori.checkpoint import parse_text
from hoshitori.ids import natural_id
from hoshitori.ledger import Ledger
from hoshitori.note import split_note
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339
from hoshitori.timing import LEVEL_C3
from hoshitori.trusted_root import TimeStampRules

Attestor = Callable[[bytes, int], Attestation]  # (digest to stamp, latest commit time in ms) -> what the authorities said
MAX_RECORDS: Final = 5000


class CommitError(ValueError):
    """The batch cannot be committed as asked."""


@dataclass(frozen=True)
class Committed:
    source: str
    record_id: str
    seq: int
    committed_at_ms: int
    level: str


@dataclass(frozen=True)
class Commitment:
    note: bytes
    size: int
    root: bytes
    records: tuple[Committed, ...]  # in the order submitted
    attestation: Attestation

    @property
    def uniform(self) -> bool:
        return len({r.level for r in self.records}) == 1

    @property
    def level(self) -> str:
        """The lowest level any record reached: a claim about the batch holds for all of it."""
        below = [r.level for r in self.records if r.level != LEVEL_C3]
        return below[0] if below else LEVEL_C3

    @property
    def commitment_id(self) -> str:
        return natural_id("cm", {"root": self.root.hex(), "size": self.size})

    @property
    def origin_ms(self) -> int:
        return self.attestation.origin_ms

    def level_of(self, source: str, record_id: str) -> str:
        for record in self.records:
            if (record.source, record.record_id) == (source, record_id):
                return record.level
        raise KeyError((source, record_id))


def commit_batch(
    ledger: Ledger, records: Sequence[dict[str, canonical.JsonValue]], *, attestor: Attestor, rules: TimeStampRules
) -> Commitment:
    """Submit the records, append them, sign one checkpoint over them and have it stamped; return what each record reached."""
    if not records or len(records) > MAX_RECORDS:
        raise CommitError(f"a batch has from 1 to {MAX_RECORDS} records")
    keys = [(str(r.get("source")), str(r.get("id"))) for r in records]
    if len(set(keys)) != len(keys):
        raise CommitError("a record is in a batch once")
    ledger.submit_many(records)
    ledger.append_pending()
    return attest_appended(ledger, keys, attestor=attestor, rules=rules)


def attest_appended(ledger: Ledger, keys: Sequence[tuple[str, str]], *, attestor: Attestor, rules: TimeStampRules) -> Commitment:
    """Sign one checkpoint over records that are already in the ledger and have it stamped: what ``commit_batch`` does after it appends, and
    what a process that was killed between the append and the stamp does when it starts again."""
    if not keys or len(keys) > MAX_RECORDS:
        raise CommitError(f"a batch has from 1 to {MAX_RECORDS} records")
    seqs = [cast(int, ledger.find(source, record_id)) for source, record_id in keys]
    if any(seq is None for seq in seqs):
        raise CommitError("a record to stamp is not in the ledger")
    committed = [to_ms(parse_rfc3339(str(canonical.parse_record(ledger.read_record(seq))["committed_at"]))) for seq in seqs]
    note = ledger.checkpoint()
    text, _ = split_note(note)
    checkpoint = parse_text(text)
    attestation = attestor(hashlib.sha256(text).digest(), max(committed))
    reached = []
    for (source, record_id), seq, at in zip(keys, seqs, committed, strict=True):
        reached.append(Committed(source, record_id, seq, at, attestation.timing_at(at, rules).level))
    if attestation.level == LEVEL_C3:
        ledger.register_attested(checkpoint.size, checkpoint.root, "rfc3161:" + ",".join(sorted(attestation.timing.chosen)))
    return Commitment(note, checkpoint.size, checkpoint.root, tuple(reached), attestation)
