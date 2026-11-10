"""The persistent adjudication: each command is logged with the time it was given, so a replay reproduces credit and windows exactly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoshitori import adjudication as adj
from hoshitori import adjudication_store as store_mod
from hoshitori import command_log

DAY = 86_400.0


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def open_store(
    tmp_path: Path, clock: Clock | None = None, reviewers: tuple[str, ...] = ("rev1", "rev2")
) -> tuple[store_mod.AdjudicationStore, Clock]:
    clock = clock or Clock()
    return store_mod.AdjudicationStore(tmp_path / "adj.jsonl", reviewers, clock), clock


def fields(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "network": "n-alice",
        "kind": "CORRECT_FACT",
        "claim": "claim-1",
        "url": "https://example.org/a",
        "source_class": "PRIMARY_DOCUMENT",
        "quote_found": True,
        "origin": "EXCHANGE:x",
        "checkable": True,
        "machine_result": "CONFIRMED",
    }
    body.update(over)
    return body


def snapshot(s: store_mod.AdjudicationStore) -> tuple[object, object, object]:
    return s.public_view(), s.moderation_view(), {c: (s.credit(c), s.pending_credit(c)) for c in ("alice", "bob", "carol")}


def test_reopening_reproduces_cases_decisions_and_credit_from_the_logged_times(tmp_path: Path) -> None:
    s, clock = open_store(tmp_path)
    s.submit("c1", "alice", **fields())
    clock.t += 100
    s.submit("c2", "bob", **fields(claim="claim-2", source_class="SOCIAL_POST", checkable=False, machine_result=None))
    clock.t += 100
    s.review("c2", "rev1", "REJECTED", "no support")
    clock.t += 100
    s.appeal("c2", "bob", "the quote is on page 3")
    clock.t += 100
    s.review_appeal("c2", "rev2", "ACCEPTED_CORRECTION", "the quote is there")
    clock.t += 8 * DAY
    before = snapshot(s)
    assert before[2]["alice"] == (1, 0) and before[2]["bob"] == (1, 0)  # type: ignore[index]
    again, _ = open_store(tmp_path, clock)
    assert snapshot(again) == before
    later = Clock()
    later.t = clock.t + 365 * DAY
    reopened, _ = open_store(tmp_path, later)  # a much later clock changes no decision's own time
    assert reopened.case("c1").decided_at == s.case("c1").decided_at and reopened.case("c2").history == s.case("c2").history


def test_a_refused_command_is_logged_with_the_words_and_replays_as_the_same_refusal(tmp_path: Path) -> None:
    s, _ = open_store(tmp_path)
    s.submit("c1", "alice", **fields(source_class="SOCIAL_POST", checkable=False, machine_result=None))
    with pytest.raises(adj.AdjudicationError, match="alice is not a reviewer"):
        s.review("c1", "alice", "REJECTED", "own item")
    s.record_support("a", "b")
    log = command_log.CommandLog(tmp_path / "adj.jsonl").read()
    assert [c["op"] for c in log] == ["open", "submit", "review", "record_support"]
    assert log[2]["refused"] == "alice is not a reviewer"
    again, _ = open_store(tmp_path, Clock())
    assert snapshot(again) == snapshot(s) and again.case("c1").state == "PENDING"


def test_a_store_opened_with_other_reviewers_or_another_window_is_refused(tmp_path: Path) -> None:
    open_store(tmp_path)
    with pytest.raises(store_mod.StoreError, match="the log was written for other reviewers"):
        store_mod.AdjudicationStore(tmp_path / "adj.jsonl", ("rev1",), Clock())
    with pytest.raises(store_mod.StoreError, match="the log was written for another appeal window"):
        store_mod.AdjudicationStore(tmp_path / "adj.jsonl", ("rev1", "rev2"), Clock(), appeal_window_s=1.0)


def test_a_replay_that_disagrees_with_the_log_or_an_unknown_operation_is_refused(tmp_path: Path) -> None:
    s, _ = open_store(tmp_path)
    s.submit("c1", "alice", **fields())
    path = tmp_path / "adj.jsonl"
    commands = command_log.CommandLog(path).read()
    path.unlink()
    forged = command_log.CommandLog(path)
    forged.append(commands[0])
    forged.append({**commands[1], "refused": "something else"})
    with pytest.raises(store_mod.StoreError, match=re.escape("command 2 (submit): the log says it was refused, the replay accepted it")):
        store_mod.AdjudicationStore(path, ("rev1", "rev2"), Clock())
    path.unlink()
    forged = command_log.CommandLog(path)
    forged.append(commands[0])
    forged.append({"op": "review", "case_id": "zz", "reviewer": "rev1", "outcome": "REJECTED", "reason": "x", "at": 5.0})
    with pytest.raises(
        store_mod.StoreError, match=re.escape("command 2 (review): the log says it was accepted, the replay refused it: no case zz")
    ):
        store_mod.AdjudicationStore(path, ("rev1", "rev2"), Clock())
    path.unlink()
    forged = command_log.CommandLog(path)
    forged.append(commands[0])
    forged.append({"op": "explode", "at": 1.0})
    with pytest.raises(store_mod.StoreError, match=re.escape("command 2 (explode): an operation is one of")):
        store_mod.AdjudicationStore(path, ("rev1", "rev2"), Clock())


def test_the_operations_are_pinned_and_each_is_recorded_with_its_time(tmp_path: Path) -> None:
    assert store_mod.OPS == ("submit", "review", "appeal", "review_appeal", "record_support")
    s, clock = open_store(tmp_path)
    s.submit("c1", "alice", **fields(source_class="SOCIAL_POST", checkable=False, machine_result=None))
    clock.t += 5
    s.review("c1", "rev1", "REJECTED", "no")
    clock.t += 5
    s.appeal("c1", "alice", "please")
    clock.t += 5
    s.review_appeal("c1", "rev2", "REJECTED", "still no")
    clock.t += 5
    s.record_support("x", "y")
    log = command_log.CommandLog(tmp_path / "adj.jsonl").read()
    assert [c["op"] for c in log] == ["open", "submit", "review", "appeal", "review_appeal", "record_support"]
    assert [c["at"] for c in log[1:]] == [1_000_000.0, 1_000_005.0, 1_000_010.0, 1_000_015.0, 1_000_020.0]
    assert log[0] == {"op": "open", "reviewers": ["rev1", "rev2"], "window": adj.APPEAL_WINDOW_S}


def test_reads_use_the_clock_of_the_moment(tmp_path: Path) -> None:
    s, clock = open_store(tmp_path)
    s.submit("c1", "alice", **fields())
    assert s.credit("alice") == 0 and s.pending_credit("alice") == 1
    clock.t += adj.APPEAL_WINDOW_S
    assert s.credit("alice") == 1 and s.pending_credit("alice") == 0
    assert s.group_of("alice") == "alice" and s.reviewers_for("c1") == ("rev1", "rev2") and s.pending_reason("c1") is None


def test_every_command_returns_the_case_and_every_read_returns_its_view(tmp_path: Path) -> None:
    s, clock = open_store(tmp_path)
    made = s.submit("c1", "alice", **fields(source_class="SOCIAL_POST", checkable=False, machine_result=None))
    assert made.case_id == "c1" and made.state == "PENDING" and s.pending_reason("c1") == "waiting for a reviewer"
    assert s.review("c1", "rev1", "REJECTED", "no").outcome == "REJECTED" and s.pending_reason("c1") is None
    assert s.appeal("c1", "alice", "please").state == "APPEAL_OPEN"
    assert s.review_appeal("c1", "rev2", "ACCEPTED_CORRECTION", "ok").state == "APPEAL_DECIDED"
    view = s.public_view()
    assert [row["case"] for row in view["cases"]] == ["c1"] and view["cases"][0]["outcome"] == "ACCEPTED_CORRECTION"  # type: ignore[index]
    assert s.moderation_view() == {"groups": []}
    clock.t += 1


def test_a_log_that_records_a_different_refusal_than_the_replay_gives_is_refused(tmp_path: Path) -> None:
    s, _ = open_store(tmp_path)
    s.submit("c1", "alice", **fields())
    with pytest.raises(adj.AdjudicationError):
        s.submit("c1", "bob", **fields(claim="claim-2"))  # refused: a case id is used once
    path = tmp_path / "adj.jsonl"
    commands = command_log.CommandLog(path).read()
    path.unlink()
    forged = command_log.CommandLog(path)
    for c in commands[:-1]:
        forged.append(c)
    forged.append({**commands[-1], "refused": "a different reason"})
    with pytest.raises(store_mod.StoreError) as raised:
        store_mod.AdjudicationStore(path, ("rev1", "rev2"), Clock())
    assert (
        str(raised.value)
        == "command 3 (submit): the log says it was refused as 'a different reason', the replay as 'a case id is used once'"
    )
