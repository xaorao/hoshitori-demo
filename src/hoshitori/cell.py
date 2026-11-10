"""The external compare-and-swap cell that fences failover (ADR-025).

One cell holds the *epoch* and the *pointer* (size and root of the last published checkpoint). It stands in for a
linearizable conditional write in object storage or a coordination service. The ledger never decides who the writer
is by itself; it asks the cell:

* ``publish`` releases a checkpoint only if the writer's epoch is the cell's epoch **and** the previous pointer is the
  one the writer saw, so a stale primary cannot publish after a promotion;
* ``promote`` takes the next epoch only if the epoch **and the pointer** are unchanged since the candidate proved
  continuity from that pointer. Swapping the epoch alone lost an honestly published checkpoint in a split-step model: the old primary
  published once more between the continuity proof and the swap.

``FileCell`` is exact only on one machine (an exclusive file lock plus an atomic replace). A real deployment needs a
store with a documented conditional-write guarantee; choosing it is a hosting decision . The tests use
``InMemoryCell`` and ``FileCell``; both implement the same protocol.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from hoshitori.merkle import EMPTY_ROOT

Pointer = tuple[int, bytes]
INITIAL_POINTER: Final[Pointer] = (0, EMPTY_ROOT)


@dataclass(frozen=True)
class CellState:
    epoch: int
    version: int
    pointer: Pointer


class PointerStore(Protocol):
    def read(self) -> CellState: ...

    def publish(self, epoch: int, expect: Pointer, new: Pointer) -> bool: ...

    def promote(self, expect_epoch: int, expect: Pointer, new_epoch: int) -> bool: ...


def _publish_ok(state: CellState, epoch: int, expect: Pointer, new: Pointer) -> bool:
    if state.epoch != epoch or state.pointer != expect:
        return False
    # the pointer only moves forward, and never to a different root for the same size
    return not (new[0] < expect[0] or (new[0] == expect[0] and new[1] != expect[1]))


class InMemoryCell:
    def __init__(self) -> None:
        self._state = CellState(0, 0, INITIAL_POINTER)
        self._lock = threading.Lock()

    def read(self) -> CellState:
        with self._lock:
            return self._state

    def publish(self, epoch: int, expect: Pointer, new: Pointer) -> bool:
        with self._lock:
            if not _publish_ok(self._state, epoch, expect, new):
                return False
            self._state = CellState(self._state.epoch, self._state.version + 1, new)
            return True

    def promote(self, expect_epoch: int, expect: Pointer, new_epoch: int) -> bool:
        with self._lock:
            if self._state.epoch != expect_epoch or self._state.pointer != expect or new_epoch != expect_epoch + 1:
                return False
            self._state = CellState(new_epoch, self._state.version + 1, self._state.pointer)
            return True


class FileCell:
    """A cell in a file, serialised by an exclusive lock. Exact on one machine only."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> CellState:
        if not self.path.exists():
            return CellState(0, 0, INITIAL_POINTER)
        raw = json.loads(self.path.read_text())
        return CellState(int(raw["epoch"]), int(raw["version"]), (int(raw["size"]), bytes.fromhex(raw["root"])))

    def _write(self, state: CellState) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {"epoch": state.epoch, "version": state.version, "size": state.pointer[0], "root": state.pointer[1].hex()}
        with open(tmp, "w") as handle:
            handle.write(json.dumps(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        dir_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def read(self) -> CellState:
        with self._locked():
            return self._read()

    def publish(self, epoch: int, expect: Pointer, new: Pointer) -> bool:
        with self._locked():
            state = self._read()
            if not _publish_ok(state, epoch, expect, new):
                return False
            self._write(CellState(state.epoch, state.version + 1, new))
            return True

    def promote(self, expect_epoch: int, expect: Pointer, new_epoch: int) -> bool:
        with self._locked():
            state = self._read()
            if state.epoch != expect_epoch or state.pointer != expect or new_epoch != expect_epoch + 1:
                return False
            self._write(CellState(new_epoch, state.version + 1, state.pointer))
            return True
