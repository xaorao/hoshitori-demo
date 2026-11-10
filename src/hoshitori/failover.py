"""Fenced promotion of a replica (ADR-025) and the verifier's acceptance rule for published checkpoints.

Failover is a manual act, never an election:

1. the candidate opens its replica and checks **continuity**: its log must contain the last published checkpoint
   (same size, same root); otherwise it fails closed;
2. it takes the next epoch with a compare-and-swap over the epoch **and the pointer**, so a checkpoint the old
   primary published after the continuity check makes the swap fail and the candidate must resynchronise;
3. it records the epoch change in its own log as an ``epoch`` record that fixes the closing size of the epoch before;
4. every checkpoint it releases goes through the cell with its epoch and the previous pointer.

A verifier accepts a published checkpoint only if it is consistent with the chain already accepted and, when it was
signed under an epoch that has closed, its size does not exceed that epoch's closing size (``PublishedChain``).
"""

from __future__ import annotations

import json
from pathlib import Path

from hoshitori import envelope
from hoshitori.cell import PointerStore
from hoshitori.clock import Clock
from hoshitori.ledger import DEFAULT_SEGMENT_RECORDS, EPOCH_TYPE, LEDGER_SOURCE, Ledger, LedgerError
from hoshitori.merkle import verify_consistency
from hoshitori.note import Signer
from hoshitori.timeutil import format_rfc3339_ms


class ContinuityError(LedgerError):
    """The candidate's log does not contain the last published checkpoint."""


class PromotionLost(LedgerError):
    """Another candidate, or a publication by the old primary, won the compare-and-swap first."""


class Rejected(ValueError):
    """A published checkpoint failed the acceptance rule."""


def promote(
    directory: str | Path,
    cell: PointerStore,
    *,
    signer: Signer,
    origin: str,
    clock: Clock,
    segment_records: int = DEFAULT_SEGMENT_RECORDS,
) -> Ledger:
    """Promote the replica in ``directory`` to writer of the next epoch; returns its open ledger.

    Safe to call again after a crash between the swap and the epoch record: the intent is stored before the swap and
    completed when the cell shows that this candidate won.
    """
    led = Ledger.open(directory, signer=signer, origin=origin, clock=clock, segment_records=segment_records)
    try:
        if led.state != "OK":
            raise LedgerError(f"the replica is in {led.state}: {led.state_detail}")
        intent_path = Path(directory) / "PROMOTING"
        state = cell.read()
        target = state.epoch + 1
        if intent_path.exists():
            target = int(json.loads(intent_path.read_text())["target"])
            if state.epoch != target:
                intent_path.unlink()  # we did not win, or the swap never happened; start again
                target = state.epoch + 1
        size, root = state.pointer
        if size > 0 and (led.size < size or led.root(size) != root):
            raise ContinuityError(f"the replica ({led.size} records) does not contain the last published checkpoint of size {size}")
        if state.epoch != target:
            intent_path.write_text(json.dumps({"target": target, "pointer_size": size}))
            if not cell.promote(state.epoch, state.pointer, target):
                intent_path.unlink(missing_ok=True)
                raise PromotionLost("the epoch or the pointer changed during promotion")
        led.epoch = target
        epoch_id = f"epoch-{target}"
        if led.find(LEDGER_SOURCE, epoch_id) is None:
            led.submit(
                {
                    "spec": envelope.SPEC,
                    "type": EPOCH_TYPE,
                    "type_version": 1,
                    "id": epoch_id,
                    "source": LEDGER_SOURCE,
                    "created_at": format_rfc3339_ms(clock.now()),
                    "body": {"epoch": target, "previous_epoch": target - 1, "closing_size": size, "closing_root": root.hex()},
                }
            )
            led.append_pending()
        intent_path.unlink(missing_ok=True)
        led.publish_checkpoint(cell)
        return led
    except BaseException:
        led.abandon()
        raise


class PublishedChain:
    """The chain of published checkpoints a verifier has accepted."""

    def __init__(self) -> None:
        self.accepted: list[tuple[int, bytes]] = []
        self.closing: dict[int, int] = {}

    def close_epoch(self, epoch: int, closing_size: int) -> None:
        """Record the closing size of ``epoch`` (taken from a verified ``epoch`` record of the next epoch)."""
        if epoch in self.closing and self.closing[epoch] != closing_size:
            raise Rejected(f"epoch {epoch} already closed at {self.closing[epoch]}")
        self.closing[epoch] = closing_size

    def accept(self, size: int, root: bytes, signing_epoch: int, proof: list[bytes] | None = None) -> None:
        """Accept a checkpoint signed under ``signing_epoch``; ``proof`` links it to the latest accepted one."""
        if signing_epoch in self.closing and size > self.closing[signing_epoch]:
            raise Rejected(f"size {size} exceeds the closing size {self.closing[signing_epoch]} of epoch {signing_epoch}")
        if not self.accepted:
            self.accepted.append((size, root))
            return
        latest_size, latest_root = self.accepted[-1]
        if size == latest_size:
            if root != latest_root:
                raise Rejected("a different root for an accepted size: the history forked")
            return
        if proof is None:
            raise Rejected("a consistency proof is required")
        if size > latest_size:
            if not verify_consistency(latest_size, size, proof, latest_root, root):
                raise Rejected("not consistent with the accepted chain")
            self.accepted.append((size, root))
        elif not verify_consistency(size, latest_size, proof, root, latest_root):
            raise Rejected("not a prefix of the accepted chain")
