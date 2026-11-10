"""The lesson book on disk: every command the book is given is written to a command log, and reopening replays them.

The book is deterministic and has no clock, so its commands are its whole state. A command that the book refused is logged with
the refusal, and the replay must refuse it in the same words: a log whose replay disagrees with what it says happened is
refused, not trusted. A log is bound to the protocol and the root version it was started under.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from hoshitori import lessons
from hoshitori.command_log import CommandLog

OPS: Final = ("propose", "start_test", "record_day", "decide", "holdback_day")


class StoreError(ValueError):
    """A log that does not belong to this protocol, or whose replay differs from what it records."""


def _lesson_to_record(lesson: lessons.Lesson) -> dict[str, Any]:
    return {
        "lesson_id": lesson.lesson_id,
        "error_class": lesson.error_class,
        "change": dict(lesson.change),
        "source_cases": [[case, day] for case, day in lesson.source_cases],
    }


def _lesson_from_record(record: dict[str, Any]) -> lessons.Lesson:
    cases = tuple((str(case), int(day)) for case, day in record["source_cases"])
    return lessons.Lesson(str(record["lesson_id"]), str(record["error_class"]), dict(record["change"]), cases)


class LessonStore:
    def __init__(self, path: Path, protocol: lessons.Protocol, root: str) -> None:
        self._log = CommandLog(path)
        self.book = lessons.Book(protocol, root)
        commands = self._log.read()
        if not commands:
            self._log.append({"op": "open", "protocol": protocol.hash(), "root": root})
            return
        first = commands[0]
        if first.get("op") != "open" or first.get("protocol") != protocol.hash():
            raise StoreError("the log was written under another protocol")
        if first.get("root") != root:
            raise StoreError("the log was written for another root version")
        for number, command in enumerate(commands[1:], start=2):
            self._replay(number, command)

    # --- replay -------------------------------------------------------------------------------------------------------------------------
    def _apply(self, command: dict[str, Any]) -> None:
        op = command.get("op")
        book = self.book
        actions: dict[str, Callable[[], None]] = {
            "propose": lambda: book.propose(_lesson_from_record(command["lesson"])),
            "start_test": lambda: book.start_test(command["name"], command["first_day"]),
            "record_day": lambda: book.record_day(
                command["name"], command["day"], command["candidate"], command["parent"], command["null"]
            ),
            "decide": lambda: book.decide(command["name"], command.get("guards")),
            "holdback_day": lambda: book.holdback_day(
                command["name"], command["day"], command["candidate"], command["parent"], command.get("guards")
            ),
        }
        if op not in actions:
            raise StoreError(f"an operation is one of {list(OPS)}")
        actions[op]()

    def _replay(self, number: int, command: dict[str, Any]) -> None:
        op = command.get("op")
        try:
            self._apply(command)
            outcome = None
        except lessons.LessonError as exc:
            outcome = str(exc)
        except StoreError as exc:
            raise StoreError(f"command {number} ({op}): {exc}") from None
        logged = command.get("refused")
        if logged is None and outcome is not None:
            raise StoreError(f"command {number} ({op}): the log says it was accepted, the replay refused it: {outcome}")
        if logged is not None and outcome is None:
            raise StoreError(f"command {number} ({op}): the log says it was refused, the replay accepted it")
        if logged is not None and logged != outcome:
            raise StoreError(f"command {number} ({op}): the log says it was refused as {logged!r}, the replay as {outcome!r}")

    # --- the commands -------------------------------------------------------------------------------------------------------------------
    def _do(self, command: dict[str, Any]) -> None:
        try:
            self._apply(command)
        except lessons.LessonError as exc:
            self._log.append({**command, "refused": str(exc)})
            raise
        self._log.append(command)

    def propose(self, lesson: lessons.Lesson) -> None:
        self._do({"op": "propose", "lesson": _lesson_to_record(lesson)})

    def start_test(self, name: str, first_day: int) -> None:
        self._do({"op": "start_test", "name": name, "first_day": first_day})

    def record_day(self, name: str, day: int, candidate: float, parent: float, null: float) -> None:
        self._do({"op": "record_day", "name": name, "day": day, "candidate": candidate, "parent": parent, "null": null})

    def decide(self, name: str, guards: dict[str, bool] | None = None) -> None:
        self._do({"op": "decide", "name": name, "guards": guards})

    def holdback_day(self, name: str, day: int, candidate: float, parent: float, guards: dict[str, bool] | None = None) -> None:
        self._do({"op": "holdback_day", "name": name, "day": day, "candidate": candidate, "parent": parent, "guards": guards})

    def public_ledger(self) -> dict[str, Any]:
        return {**self.book.ledger(), "log_head": self._log.head()}
