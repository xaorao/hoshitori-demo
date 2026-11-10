"""An append-only, hash-chained file of the commands a state machine was given.

A machine that is rebuilt by replaying its commands never has a second copy of its state that can drift: the log is the state.
Each line holds a sequence number, the hash of the line before, the command and the hash of both. An edited, removed or
reordered line breaks the chain and is named by its line. A last line that was cut off by a crash was never acknowledged, so it
is ignored and the next append removes it. Anything wrong in the middle of the file is an error, never skipped.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Final

GENESIS: Final = "0" * 64


class LogError(ValueError):
    """A log that is damaged, or a command that cannot be logged."""


def _text(command: dict[str, Any]) -> str:
    return json.dumps(command, sort_keys=True, separators=(",", ":"))


def _hash(previous: str, command: dict[str, Any]) -> str:
    return hashlib.sha256((previous + _text(command)).encode()).hexdigest()


class CommandLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _scan(self) -> tuple[list[dict[str, Any]], str, int]:
        """The verified commands, the hash of the last, and how many bytes of the file are whole lines."""
        if not self.path.exists():
            return [], GENESIS, 0
        raw = self.path.read_bytes()
        whole = raw.rfind(b"\n") + 1  # a last line without its newline was cut off
        previous, commands = GENESIS, []
        for number, line in enumerate(raw[:whole].decode().splitlines(), start=1):
            try:
                row = json.loads(line)
            except ValueError:
                raise LogError(f"line {number}: not a log line") from None
            fits = (
                isinstance(row, dict)
                and isinstance(row.get("seq"), int)
                and isinstance(row.get("prev"), str)
                and isinstance(row.get("command"), dict)
                and isinstance(row.get("hash"), str)
            )
            if not fits:
                raise LogError(f"line {number}: not a log line")
            if row["prev"] != previous:
                raise LogError(f"line {number}: the chain is broken")
            if row["hash"] != _hash(previous, row["command"]):
                raise LogError(f"line {number}: the hash does not match its command")
            if row["seq"] != number - 1:
                raise LogError(f"line {number}: the sequence number is {row['seq']}, expected {number - 1}")
            commands.append(row["command"])
            previous = row["hash"]
        return commands, previous, whole

    def read(self) -> list[dict[str, Any]]:
        return self._scan()[0]

    def head(self) -> str:
        return self._scan()[1]

    def append(self, command: dict[str, Any]) -> int:
        """Add a command, after the last whole line, and return its sequence number once it is on disk."""
        if not isinstance(command, dict):
            raise LogError("a command is a JSON object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                commands, previous, whole = self._scan()
                handle.truncate(whole)
                handle.seek(0, os.SEEK_END)
                row = {"seq": len(commands), "prev": previous, "command": command, "hash": _hash(previous, command)}
                handle.write((_text(row) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
                return len(commands)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
