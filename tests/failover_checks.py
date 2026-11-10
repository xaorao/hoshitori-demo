"""Scenarios for the cell, the epoch records, fenced promotion and the acceptance rule (ADR-025)."""

from __future__ import annotations

import shutil
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

from hoshitori import canonical, merkle
from hoshitori.cell import InMemoryCell
from hoshitori.clock import ManualClock
from tests.ledger_checks import ORIGIN, SIGNER, START, SimulatedCrash, fill, invariants, rec

Mods = types.SimpleNamespace  # .ledger, .failover, .cell


def open_primary(mods: Mods, directory: Path, records: int = 100):  # type: ignore[no-untyped-def]
    return mods.ledger.Ledger.open(directory, signer=SIGNER, origin=ORIGIN, clock=ManualClock(START), segment_records=records)


def copy_log(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("LOCK", "PROMOTING", "quarantine"))


class HookedCell:
    """A cell whose promote() first runs a callback (to interleave another actor exactly between check and swap)."""

    def __init__(self, inner, before_promote=None, after_promote=None):  # type: ignore[no-untyped-def]
        self.inner, self.before_promote, self.after_promote = inner, before_promote, after_promote

    def read(self):  # type: ignore[no-untyped-def]
        return self.inner.read()

    def publish(self, epoch, expect, new):  # type: ignore[no-untyped-def]
        return self.inner.publish(epoch, expect, new)

    def promote(self, expect_epoch, expect, new_epoch):  # type: ignore[no-untyped-def]
        if self.before_promote is not None:
            callback, self.before_promote = self.before_promote, None
            callback()
        result = self.inner.promote(expect_epoch, expect, new_epoch)
        if result and self.after_promote is not None:
            self.after_promote()
        return result


def promote(mods: Mods, directory: Path, cell, records: int = 100):  # type: ignore[no-untyped-def]
    return mods.failover.promote(directory, cell, signer=SIGNER, origin=ORIGIN, clock=ManualClock(START), segment_records=records)


# ---------------------------------------------------------------------------------------------------------------------
def cell_semantics(mods: Mods, tmp: Path) -> list[str]:
    bad: list[str] = []
    root = lambda n: bytes([n]) * 32  # noqa: E731
    for label, cell in (("memory", mods.cell.InMemoryCell()), ("file", mods.cell.FileCell(tmp / "cell.json"))):
        initial = cell.read()
        if (initial.epoch, initial.pointer) != (0, mods.cell.INITIAL_POINTER):
            bad.append(f"{label}: wrong initial state")
        if not cell.publish(0, initial.pointer, (3, root(3))):
            bad.append(f"{label}: a valid publication was refused")
        if cell.publish(0, initial.pointer, (4, root(4))):
            bad.append(f"{label}: a publication with a stale previous pointer was accepted")
        if cell.publish(1, (3, root(3)), (4, root(4))):
            bad.append(f"{label}: a publication under another epoch was accepted")
        if cell.publish(0, (3, root(3)), (2, root(2))):
            bad.append(f"{label}: the pointer moved backwards")
        if cell.publish(0, (3, root(3)), (3, root(9))):
            bad.append(f"{label}: a different root for the same size was accepted")
        if cell.promote(0, initial.pointer, 1):
            bad.append(f"{label}: a promotion with a stale pointer was accepted (the epoch-only swap)")
        if cell.promote(0, (3, root(3)), 2):
            bad.append(f"{label}: an epoch was skipped")
        if not cell.promote(0, (3, root(3)), 1):
            bad.append(f"{label}: a valid promotion was refused")
        if cell.promote(0, (3, root(3)), 1):
            bad.append(f"{label}: the same promotion succeeded twice")
        if cell.publish(0, (3, root(3)), (4, root(4))):
            bad.append(f"{label}: the old epoch could still publish")
        if not cell.publish(1, (3, root(3)), (4, root(4))):
            bad.append(f"{label}: the new epoch could not publish")
    again = mods.cell.FileCell(tmp / "cell.json").read()
    if (again.epoch, again.pointer) != (1, (4, root(4))):
        bad.append("the file cell did not persist its state")
    return bad


def normal_failover(mods: Mods, tmp: Path) -> list[str]:
    bad: list[str] = []
    cell = InMemoryCell()
    primary = open_primary(mods, tmp / "primary")
    fill(primary, 6)
    primary.publish_checkpoint(cell)
    fill(primary, 2, start=20)  # appended after the last publication, and copied to the replica
    primary.abandon()
    copy_log(tmp / "primary", tmp / "replica")
    primary = open_primary(mods, tmp / "primary")
    new = promote(mods, tmp / "replica", cell)
    state = cell.read()
    if state.epoch != 1 or new.epoch != 1:
        bad.append(f"epoch after promotion: cell {state.epoch}, ledger {new.epoch}")
    epochs = new.epoch_records()
    if len(epochs) != 1 or epochs[0]["body"]["closing_size"] != 6:  # type: ignore[index]
        bad.append("the epoch record does not fix the closing size of the previous epoch")
    fill(new, 3, start=100)
    new.publish_checkpoint(cell)

    # the old primary does not know it was replaced; whatever it does, it cannot publish
    fill(primary, 8, start=50)  # longer than anything the new epoch has published, so only the epoch can stop it
    try:
        primary.publish_checkpoint(cell)
        bad.append("a stale primary published a checkpoint after the promotion")
    except mods.ledger.Fenced:
        pass
    if primary.state != "FENCED":
        bad.append("a fenced-off writer was not stopped")
    try:
        primary.submit(rec(77))
        bad.append("a fenced-off writer accepted a command")
    except mods.ledger.IntegrityFailure:
        pass
    bad += invariants(tmp / "replica", new)
    primary.abandon()
    new.abandon()
    return bad


def continuity_and_races(mods: Mods, tmp: Path) -> list[str]:
    bad: list[str] = []
    # a replica that is behind the last published checkpoint cannot be promoted
    cell = InMemoryCell()
    primary = open_primary(mods, tmp / "p1")
    fill(primary, 4)
    primary.abandon()
    copy_log(tmp / "p1", tmp / "behind")  # copied at 4 records
    primary = open_primary(mods, tmp / "p1")
    fill(primary, 3, start=4)
    primary.publish_checkpoint(cell)  # pointer at 7
    primary.abandon()
    try:
        promote(mods, tmp / "behind", cell)
        bad.append("a replica missing published records was promoted")
    except mods.failover.ContinuityError:
        pass
    if cell.read().epoch != 0:
        bad.append("a failed continuity check changed the epoch")
    # a replica with a different history cannot be promoted either
    other = open_primary(mods, tmp / "other")
    for i in range(7):
        other.submit(rec(i, extra="another history"))
    other.append_pending()
    other.abandon()
    try:
        promote(mods, tmp / "other", cell)
        bad.append("a replica with another history was promoted")
    except mods.failover.ContinuityError:
        pass

    # two candidates read the same epoch; only one swap can win
    cell2 = InMemoryCell()
    p = open_primary(mods, tmp / "p2")
    fill(p, 5)
    p.publish_checkpoint(cell2)
    p.abandon()
    copy_log(tmp / "p2", tmp / "r1")
    copy_log(tmp / "p2", tmp / "r2")
    winners = []

    def first_candidate_wins() -> None:
        winners.append(promote(mods, tmp / "r1", cell2))

    racing = HookedCell(cell2, before_promote=first_candidate_wins)
    try:
        promote(mods, tmp / "r2", racing)
        bad.append("two simultaneous promotions both succeeded")
    except mods.failover.PromotionLost:
        pass
    if cell2.read().epoch != 1:
        bad.append("the epoch after a simultaneous promotion is not 1")
    for w in winners:
        w.abandon()

    # the old primary publishes between the continuity check and the swap
    cell3 = InMemoryCell()
    q = open_primary(mods, tmp / "p3")
    fill(q, 5)
    q.publish_checkpoint(cell3)
    q.abandon()
    copy_log(tmp / "p3", tmp / "r3")
    q = open_primary(mods, tmp / "p3")

    def primary_publishes_once_more() -> None:
        fill(q, 3, start=5)
        q.publish_checkpoint(cell3)

    try:
        promote(mods, tmp / "r3", HookedCell(cell3, before_promote=primary_publishes_once_more))
        bad.append("the swap succeeded although the old primary published after the continuity check")
    except mods.failover.PromotionLost:
        pass
    if cell3.read().epoch != 0 or cell3.read().pointer[0] != 8:
        bad.append("the old primary's publication was lost or the epoch moved")
    q.abandon()
    return bad


def crash_during_promotion(mods: Mods, tmp: Path) -> list[str]:
    bad: list[str] = []
    cell = InMemoryCell()
    p = open_primary(mods, tmp / "p")
    fill(p, 5)
    p.publish_checkpoint(cell)
    p.abandon()
    copy_log(tmp / "p", tmp / "r")

    def die() -> None:
        raise SimulatedCrash("after the swap, before the epoch record")

    try:
        promote(mods, tmp / "r", HookedCell(cell, after_promote=die))
        bad.append("setup: the simulated crash did not happen")
    except SimulatedCrash:
        pass
    if cell.read().epoch != 1:
        bad.append("setup: the swap did not happen")
    resumed = promote(mods, tmp / "r", cell)
    epochs = resumed.epoch_records()
    if len(epochs) != 1 or resumed.epoch != 1:
        bad.append(f"a resumed promotion wrote {len(epochs)} epoch records at epoch {resumed.epoch}")
    if cell.read().epoch != 1:
        bad.append("a resumed promotion changed the epoch again")
    if cell.read().pointer[0] != resumed.size:
        bad.append("the resumed writer did not publish its checkpoint")
    bad += invariants(tmp / "r", resumed)
    resumed.abandon()
    return bad


def acceptance_rule(mods: Mods, tmp: Path) -> list[str]:
    bad: list[str] = []
    f = mods.failover
    tree = merkle.MerkleTree(merkle.leaf_hash(bytes([i])) for i in range(12))
    forked = merkle.MerkleTree(
        [*[merkle.leaf_hash(bytes([i])) for i in range(6)], *[merkle.leaf_hash(b"x" + bytes([i])) for i in range(6)]]
    )

    def rejected(label: str, chain, *args) -> None:  # type: ignore[no-untyped-def]
        try:
            chain.accept(*args)
            bad.append(f"{label}: accepted")
        except f.Rejected:
            pass

    chain = f.PublishedChain()
    chain.accept(6, tree.root(6), 0)
    chain.close_epoch(0, 6)
    chain.accept(9, tree.root(9), 1, tree.consistency_proof(6, 9))  # the next epoch continues the chain
    chain.accept(6, tree.root(6), 0, tree.consistency_proof(6, 9))  # a stale primary's harmless old checkpoint within its closing size
    chain.accept(4, tree.root(4), 0, tree.consistency_proof(4, 9))  # a smaller one, as a prefix of the chain
    rejected("a checkpoint beyond the closing size of its epoch", chain, 8, tree.root(8), 0, tree.consistency_proof(8, 9))
    rejected("a forked checkpoint of an accepted size", chain, 9, forked.root(9), 1, None)
    rejected("a forked checkpoint with a forged proof", chain, 10, forked.root(10), 1, forked.consistency_proof(9, 10))
    rejected("a larger checkpoint without a proof", chain, 11, tree.root(11), 1, None)
    rejected(
        "a smaller checkpoint that is not a prefix",
        chain,
        5,
        forked.root(5) if forked.root(5) != tree.root(5) else b"\x01" * 32,
        1,
        tree.consistency_proof(5, 9),
    )
    try:
        chain.close_epoch(0, 7)
        bad.append("an epoch was closed twice with different sizes")
    except f.Rejected:
        pass
    return bad


def _canonical_roundtrip(body: dict[str, canonical.JsonValue]) -> bytes:  # pragma: no cover - helper for debugging
    return canonical.dumps(body)


SCENARIOS: dict[str, Callable[[Mods, Path], list[str]]] = {
    "cell_semantics": cell_semantics,
    "normal_failover": normal_failover,
    "continuity_and_races": continuity_and_races,
    "crash_during_promotion": crash_during_promotion,
    "acceptance_rule": acceptance_rule,
}


def failures(mods: Mods, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-{name}-"))
        try:
            bad += [f"{name}: {m}" for m in scenario(mods, tmp)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
