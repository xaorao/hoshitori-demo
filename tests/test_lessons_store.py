"""The persistent lesson book: every command is logged before it is applied, and reopening replays them to the same state."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from hoshitori import command_log, lessons, lessons_store

PROTOCOL = lessons.Protocol(endpoint_days=60, alpha=0.05, sigma=1.0, tau=0.5, smallest_effect=0.5, min_days=10, margin=0.2)
CHANGE = {"kind": "ABSTAIN_WHEN_CHANCE_NEAR_ONE_HALF", "points": "5"}


def lesson(name: str = "l1", days: tuple[int, ...] | None = None, change: dict[str, str] | None = None) -> lessons.Lesson:
    return lessons.Lesson(name, "LOW_CONVICTION", change or CHANGE, tuple((f"c{k}", d) for k, d in enumerate(days or tuple(range(12)))))


def open_store(tmp_path: Path) -> lessons_store.LessonStore:
    return lessons_store.LessonStore(tmp_path / "lessons.jsonl", PROTOCOL, "v0")


def feed(store: lessons_store.LessonStore, name: str, days: range, effect: float, seed: int = 1) -> None:
    rng = random.Random(seed)
    for d in days:
        if store.book.status(name) != "TESTING":
            break
        base = rng.gauss(0, 1)
        store.record_day(name, d, base + effect + rng.gauss(0, 1), base, base + rng.gauss(0, 1))


def state(store: lessons_store.LessonStore) -> tuple[object, object, object, object]:
    b = store.book
    return b.ledger(), b.events(), {h: (v.parent, v.status, v.diff) for h, v in b.versions().items()}, b.live()


def test_reopening_replays_every_command_to_the_same_state(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson())
    store.start_test("l1", 100)
    feed(store, "l1", range(100, 160), 1.5)
    store.decide("l1", guards={"coverage": False})
    for k in range(3):
        store.holdback_day("l1", 160 + k, 0.4, 0.0)
    before = state(store)
    assert store.book.status("l1") == "PROMOTED"
    again = open_store(tmp_path)
    assert state(again) == before
    again.holdback_day("l1", 163, 0.4, 0.0)  # a reopened store carries on
    assert (
        again.book.status("l1") == "PROMOTED"
        and len(command_log.CommandLog(tmp_path / "lessons.jsonl").read()) == 1 + 1 + 1 + 60 + 1 + 3 + 1
    )


def test_a_refused_command_is_logged_with_its_reason_and_replays_to_the_same_refusal(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson("short", days=tuple(range(9)) + (100,) * 1 + (101, 102)))
    with pytest.raises(lessons.LessonError, match="overlap"):
        store.start_test("short", 100)
    assert store.book.status("short") == "REJECTED"
    with pytest.raises(lessons.LessonError):
        store.propose(lesson("short"))
    log = command_log.CommandLog(tmp_path / "lessons.jsonl").read()
    refused = [c for c in log if "refused" in c]
    assert [c["op"] for c in refused] == ["start_test", "propose"]
    assert refused[0]["refused"] == "the source cases overlap the validation period"
    again = open_store(tmp_path)
    assert again.book.status("short") == "REJECTED" and again.book.reason("short") == "OVERLAP"
    assert state(again) == state(store)


def test_a_store_opened_under_another_protocol_is_refused(tmp_path: Path) -> None:
    open_store(tmp_path).propose(lesson())
    other = lessons.Protocol(**{**PROTOCOL.__dict__, "alpha": 0.01})
    with pytest.raises(lessons_store.StoreError, match="the log was written under another protocol"):
        lessons_store.LessonStore(tmp_path / "lessons.jsonl", other, "v0")
    with pytest.raises(lessons_store.StoreError, match="the log was written for another root version"):
        lessons_store.LessonStore(tmp_path / "lessons.jsonl", PROTOCOL, "v9")


def test_a_replay_that_disagrees_with_the_log_is_refused(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson())
    path = tmp_path / "lessons.jsonl"
    log = command_log.CommandLog(path)
    commands = log.read()
    # rebuild the file with a command that was accepted marked as refused: the chain is valid, the replay is not
    path.unlink()
    forged = command_log.CommandLog(path)
    forged.append(commands[0])
    forged.append({**commands[1], "refused": "something else"})
    with pytest.raises(
        lessons_store.StoreError, match=re.escape("command 2 (propose): the log says it was refused, the replay accepted it")
    ):
        lessons_store.LessonStore(path, PROTOCOL, "v0")
    path.unlink()
    forged = command_log.CommandLog(path)
    forged.append(commands[0])
    forged.append({"op": "start_test", "name": "nope", "first_day": 1})  # accepted in the log, refused in the replay
    with pytest.raises(
        lessons_store.StoreError,
        match=re.escape("command 2 (start_test): the log says it was accepted, the replay refused it: no lesson nope"),
    ):
        lessons_store.LessonStore(path, PROTOCOL, "v0")


def test_an_unknown_operation_in_the_log_is_refused(tmp_path: Path) -> None:
    open_store(tmp_path)
    path = tmp_path / "lessons.jsonl"
    command_log.CommandLog(path).append({"op": "explode"})
    with pytest.raises(lessons_store.StoreError, match=re.escape("command 2 (explode): an operation is one of")):
        lessons_store.LessonStore(path, PROTOCOL, "v0")


def test_the_public_ledger_of_a_store_is_the_book_s_with_the_log_head(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson())
    ledger = store.public_ledger()
    assert (
        ledger["proposed"] == 1
        and ledger["log_head"] == command_log.CommandLog(tmp_path / "lessons.jsonl").head()
        and len(ledger["log_head"]) == 64
    )
    assert ledger["lessons"][0]["lesson"] == "l1"


def test_guard_rails_given_to_a_decision_and_a_holdback_day_survive_a_reopen(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson())
    store.start_test("l1", 100)
    feed(store, "l1", range(100, 160), 1.5)
    store.decide("l1", guards={"coverage": True})
    assert store.book.status("l1") == "INCONCLUSIVE" and store.book.reason("l1") == "GUARD:coverage"
    assert open_store(tmp_path).book.reason("l1") == "GUARD:coverage"
    other = lessons_store.LessonStore(tmp_path / "other.jsonl", PROTOCOL, "v0")
    other.propose(lesson("l2"))
    other.start_test("l2", 100)
    feed(other, "l2", range(100, 160), 1.5)
    other.decide("l2")
    other.holdback_day("l2", 160, 5.0, 0.0, guards={"risk_limit_breach": True})
    assert other.book.status("l2") == "ROLLED_BACK"
    reopened = lessons_store.LessonStore(tmp_path / "other.jsonl", PROTOCOL, "v0")
    assert reopened.book.status("l2") == "ROLLED_BACK" and reopened.book.reason("l2") == "GUARD:risk_limit_breach"


def test_the_operations_are_pinned_and_each_one_is_replayed(tmp_path: Path) -> None:
    assert lessons_store.OPS == ("propose", "start_test", "record_day", "decide", "holdback_day")
    store = open_store(tmp_path)
    store.propose(lesson())
    store.start_test("l1", 100)
    feed(store, "l1", range(100, 160), 1.5)
    store.decide("l1")
    store.holdback_day("l1", 160, 0.3, 0.0)
    ops = [c["op"] for c in command_log.CommandLog(tmp_path / "lessons.jsonl").read()]
    assert (
        ops[:3] == ["open", "propose", "start_test"] and ops[-2:] == ["decide", "holdback_day"] and set(ops) == {"open", *lessons_store.OPS}
    )


def test_a_log_that_records_a_different_refusal_than_the_replay_gives_is_refused(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    store.propose(lesson())
    with pytest.raises(lessons.LessonError):
        store.propose(lesson())  # refused: a lesson id is used once
    path = tmp_path / "lessons.jsonl"
    commands = command_log.CommandLog(path).read()
    path.unlink()
    forged = command_log.CommandLog(path)
    for c in commands[:-1]:
        forged.append(c)
    forged.append({**commands[-1], "refused": "a different reason"})
    with pytest.raises(lessons_store.StoreError) as raised:
        lessons_store.LessonStore(path, PROTOCOL, "v0")
    assert (
        str(raised.value)
        == "command 3 (propose): the log says it was refused as 'a different reason', the replay as 'a lesson id is used once'"
    )
