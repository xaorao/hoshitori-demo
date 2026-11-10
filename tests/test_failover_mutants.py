"""Each rule of fenced promotion is protected by a scenario that fails when the rule is broken.

One guard is redundant and has no mutant: ``accept`` refuses a missing proof explicitly, and ``verify_consistency``
would refuse the same input.
"""

from __future__ import annotations

import types

import pytest

from hoshitori import cell, failover, ledger
from tests.failover_checks import failures
from tests.mutation import mutant

REAL = {"ledger": ledger, "failover": failover, "cell": cell}

MUTATIONS = {
    "continuity is not checked": ("failover", "if size > 0 and (led.size < size or led.root(size) != root):", "if False:"),
    "the epoch swap is not performed": ("failover", "if not cell.promote(state.epoch, state.pointer, target):", "if False:"),
    "no epoch record is written": ("failover", "if led.find(LEDGER_SOURCE, epoch_id) is None:", "if False:"),
    "the closing size is the whole log": ('"closing_size": size,', '"closing_size": led.size,'),
    "a resumed promotion forgets its intent": (
        "failover",
        "        if intent_path.exists():\n            target =",
        "        if False:\n            target =",
    ),
    "the closing size of an epoch is not enforced": (
        "failover",
        "if signing_epoch in self.closing and size > self.closing[signing_epoch]:",
        "if False:",
    ),
    "a fork of an accepted size is tolerated": (
        "failover",
        "            if root != latest_root:\n                raise Rejected",
        "            if False:\n                raise Rejected",
    ),
    "a larger checkpoint is not checked against the chain": (
        "failover",
        "            if not verify_consistency(latest_size, size, proof, latest_root, root):",
        "            if False:",
    ),
    "a smaller checkpoint need not be a prefix": (
        "failover",
        "        elif not verify_consistency(size, latest_size, proof, root, latest_root):",
        "        elif False:",
    ),
    "an epoch can be closed twice": ("failover", "if epoch in self.closing and self.closing[epoch] != closing_size:", "if False:"),
    # cell.py
    "publish ignores the epoch": (
        "cell",
        "    if state.epoch != epoch or state.pointer != expect:\n        return False",
        "    if state.pointer != expect:\n        return False",
    ),
    "publish ignores the previous pointer": (
        "cell",
        "    if state.epoch != epoch or state.pointer != expect:\n        return False",
        "    if state.epoch != epoch:\n        return False",
    ),
    "the pointer may move backwards": (
        "cell",
        "return not (new[0] < expect[0] or (new[0] == expect[0] and new[1] != expect[1]))",
        "return True",
    ),
    "memory promote swaps the epoch alone": (
        "cell",
        "if self._state.epoch != expect_epoch or self._state.pointer != expect or new_epoch != expect_epoch + 1:",
        "if self._state.epoch != expect_epoch or new_epoch != expect_epoch + 1:",
    ),
    "file promote swaps the epoch alone": (
        "cell",
        "if state.epoch != expect_epoch or state.pointer != expect or new_epoch != expect_epoch + 1:",
        "if state.epoch != expect_epoch or new_epoch != expect_epoch + 1:",
    ),
    "an epoch can be skipped": (
        "cell",
        "if self._state.epoch != expect_epoch or self._state.pointer != expect or new_epoch != expect_epoch + 1:",
        "if self._state.epoch != expect_epoch or self._state.pointer != expect:",
    ),
    "the file cell does not persist": (
        "cell",
        "            self._write(CellState(state.epoch, state.version + 1, new))",
        "            pass",
    ),
    # ledger.py
    "a stale writer can publish": (
        "ledger",
        "if state.epoch != self.epoch or not cell.publish(self.epoch, state.pointer, new_pointer):",
        "if not cell.publish(state.epoch, state.pointer, new_pointer):",
    ),
    "a refused publication does not fence the writer": (
        "ledger",
        "self._set_state(STATE_FENCED, detail)\n                raise Fenced",
        "raise Fenced",
    ),
}


def run(target: str, old: str, new: str) -> list[str]:
    mods = types.SimpleNamespace(**REAL)
    setattr(mods, target, mutant(f"hoshitori.{target}", old, new))
    return failures(mods)


def test_real_modules_pass() -> None:
    assert failures(types.SimpleNamespace(**REAL)) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    spec = MUTATIONS[name]
    target, old, new = spec if len(spec) == 3 else ("failover", spec[0], spec[1])
    assert run(target, old, new), f"mutant not detected: {name}"
