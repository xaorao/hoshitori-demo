"""The adjudication of contributions on disk: every command is logged with the time it was given, and reopening replays them.

The adjudicator reads a clock for its decision times, appeal windows and daily caps, so a replay must see the clock the command
saw. Each command is therefore logged with its time, and a replay sets the adjudicator's clock to that time before it applies
the command; a read made later uses the clock of the moment. A command that was refused is logged with the refusal and must be
refused in the same words when replayed. A log is bound to the reviewers and the appeal window it was started with.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from hoshitori import adjudication as adj
from hoshitori.command_log import CommandLog

OPS: Final = ("submit", "review", "appeal", "review_appeal", "record_support")


class StoreError(ValueError):
    """A log that does not belong to this court, or whose replay differs from what it records."""


class AdjudicationStore:
    def __init__(
        self, path: Path, reviewers: Sequence[str], clock: Callable[[], float], appeal_window_s: float = adj.APPEAL_WINDOW_S
    ) -> None:
        self._clock = clock
        self._t = 0.0
        self._log = CommandLog(path)
        self.court = adj.Adjudicator(lambda: self._t, reviewers, appeal_window_s)
        commands = self._log.read()
        if not commands:
            self._log.append({"op": "open", "reviewers": list(reviewers), "window": appeal_window_s})
            return
        first = commands[0]
        if first.get("op") != "open" or first.get("reviewers") != list(reviewers):
            raise StoreError("the log was written for other reviewers")
        if first.get("window") != appeal_window_s:
            raise StoreError("the log was written for another appeal window")
        for number, command in enumerate(commands[1:], start=2):
            self._replay(number, command)

    # --- replay -------------------------------------------------------------------------------------------------------------------------
    def _apply(self, command: dict[str, Any]) -> None:
        op = command.get("op")
        court = self.court
        actions: dict[str, Callable[[], object]] = {
            "submit": lambda: court.submit(command["case_id"], command["contributor"], **command["fields"]),
            "review": lambda: court.review(command["case_id"], command["reviewer"], command["outcome"], command["reason"]),
            "appeal": lambda: court.appeal(command["case_id"], command["contributor"], command["reason"]),
            "review_appeal": lambda: court.review_appeal(command["case_id"], command["reviewer"], command["outcome"], command["reason"]),
            "record_support": lambda: court.record_support(command["supporter"], command["supported"]),
        }
        if op not in actions:
            raise StoreError(f"an operation is one of {list(OPS)}")
        self._t = float(command["at"])
        actions[op]()

    def _replay(self, number: int, command: dict[str, Any]) -> None:
        op = command.get("op")
        try:
            self._apply(command)
            outcome = None
        except adj.AdjudicationError as exc:
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
        command = {**command, "at": self._clock()}
        try:
            self._apply(command)
        except adj.AdjudicationError as exc:
            self._log.append({**command, "refused": str(exc)})
            raise
        self._log.append(command)

    def submit(self, case_id: str, contributor: str, **fields: Any) -> adj.Case:
        self._do({"op": "submit", "case_id": case_id, "contributor": contributor, "fields": fields})
        return self.court.case(case_id)

    def review(self, case_id: str, reviewer: str, outcome: str, reason: str) -> adj.Case:
        self._do({"op": "review", "case_id": case_id, "reviewer": reviewer, "outcome": outcome, "reason": reason})
        return self.court.case(case_id)

    def appeal(self, case_id: str, contributor: str, reason: str) -> adj.Case:
        self._do({"op": "appeal", "case_id": case_id, "contributor": contributor, "reason": reason})
        return self.court.case(case_id)

    def review_appeal(self, case_id: str, reviewer: str, outcome: str, reason: str) -> adj.Case:
        self._do({"op": "review_appeal", "case_id": case_id, "reviewer": reviewer, "outcome": outcome, "reason": reason})
        return self.court.case(case_id)

    def record_support(self, supporter: str, supported: str) -> None:
        self._do({"op": "record_support", "supporter": supporter, "supported": supported})

    # --- reads, at the clock of the moment ----------------------------------------------------------------------------------------------
    def _now(self) -> adj.Adjudicator:
        self._t = self._clock()
        return self.court

    def case(self, case_id: str) -> adj.Case:
        return self._now().case(case_id)

    def credit(self, contributor: str) -> int:
        return self._now().credit(contributor)

    def pending_credit(self, contributor: str) -> int:
        return self._now().pending_credit(contributor)

    def group_of(self, identity: str) -> str:
        return self._now().group_of(identity)

    def reviewers_for(self, case_id: str) -> tuple[str, ...]:
        return self._now().reviewers_for(case_id)

    def pending_reason(self, case_id: str) -> str | None:
        return self._now().pending_reason(case_id)

    def public_view(self) -> dict[str, Any]:
        return self._now().public_view()

    def moderation_view(self) -> dict[str, Any]:
        return self._now().moderation_view()
