"""The tag stripper of the challenge store is linear and does what the pattern it replaced did."""

from __future__ import annotations

import random
import re
import time

from hoshitori import challenge_store as cs

_OLD = re.compile(r"<(?:script|style)\b.{0,100000}?</(?:script|style)>|<[^>]{0,2000}>", re.IGNORECASE | re.DOTALL)
_PARTS = ("<", "<script", "<SCRIPT ", "</script>", "</STYLE>", "<style>", ">", "a b", "<b>", "\n", "x" * 50, "<scripts", "<script>")


def test_it_agrees_with_the_pattern_it_replaced() -> None:
    rng = random.Random(1)
    for _ in range(3000):
        text = "".join(rng.choice(_PARTS) for _ in range(rng.randint(0, 14)))
        assert cs._strip_tags(text) == _OLD.sub(" ", text), text


def test_it_agrees_at_the_limits_of_a_tag_and_of_a_block() -> None:
    """The old pattern had a limit of 2000 characters inside a tag and of 100000 after an opener: both limits, one either side, with every opener and closer."""
    cases = []
    for inner in (0, 1, 1999, 2000, 2001, 2002):
        cases.append("a<" + "x" * inner + ">b")
        cases.append("a<" + "x" * inner + "<b>c")
    for opener, closer in (
        ("<script", "</script>"),
        ("<STYLE", "</STYLE>"),
        ("<script ", "</Script>"),
        ("<style>", "</style>"),
        ("<script", "</style>"),
    ):
        for gap in (0, 1, 99_990, 99_999, 100_000, 100_001, 100_010):
            cases.append("a" + opener + "y" * gap + closer + "z")
            cases.append("a" + opener + "y" * gap + closer + "z" + opener + "q" + closer)
            cases.append("a" + opener + "y" * gap + "tail")
        cases.append("a" + opener + "<" * 5000 + closer + "z")
    cases += ["<<" + "x" * 2000 + ">", "<<" + "x" * 1999 + ">", "<<<" + "x" * 2000 + ">", "a<<>", "<<>>"]
    cases += [
        "<script</script>",
        "<scripts>x</script>",
        "<script\n</script>",
        "<style></style><script></script>",
        "</script>",
        "x</script>y",
        "<script><script></script></script>",
    ]
    for text in cases:
        assert cs._strip_tags(text) == _OLD.sub(" ", text), (len(text), text[:40])


def test_a_page_of_unclosed_openers_does_not_take_long() -> None:
    for page in ("<script " * 131072, "<style " * 131072, "<" * 1048576, "<a " * 350000):
        started = time.monotonic()
        cs._strip_tags(page)
        assert time.monotonic() - started < 3.0


# ----- the store: a torn tail, the lock during a fetch, text the record cannot hold

import threading  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from hoshitori.fetch_types import Fetched  # noqa: E402

_URL = "https://example.org/notice"
_QUOTE = "Trading in SOL will resume at 14:00 UTC"


def _page(text: str = _QUOTE) -> Fetched:
    return Fetched(_URL, 200, "text/html", "utf-8", text.encode(), "d" * 64, (), "93.184.216.34", ())


def _submission(**over: object) -> cs.Submission:
    body = {"receipt": "fc" + "a" * 26, "kind": "DISPUTE_CLAIM", "url": _URL, "quote": _QUOTE, "note": "", **over}
    return cs.read_submission(body)


def _store(tmp_path: Path) -> cs.ChallengeStore:
    return cs.ChallengeStore(tmp_path / "c.jsonl", lambda: datetime(2026, 11, 4, tzinfo=UTC))


def test_a_torn_last_line_does_not_stop_later_challenges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.file(_submission(), lambda u: _page())
    with (tmp_path / "c.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"id":"chTORN","rec')
    row, new = store.file(
        _submission(quote="Another sentence copied from the source page"), lambda u: _page("Another sentence copied from the source page")
    )
    assert new and len(store.records()) == 2
    assert row["state"] == "QUOTE_FOUND"


def test_a_slow_source_does_not_hold_other_challenges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    release = threading.Event()
    entered = threading.Event()

    def slow(url: str) -> Fetched:
        entered.set()
        release.wait(10)
        return _page()

    worker = threading.Thread(target=lambda: store.file(_submission(), slow))
    worker.start()
    assert entered.wait(5)
    other = _submission(quote="A different sentence from another source page")
    row, new = store.file(other, lambda u: _page("A different sentence from another source page"))  # must not wait for the slow fetch
    assert new and row["state"] == "QUOTE_FOUND"
    release.set()
    worker.join(10)
    assert len(store.records()) == 2


def test_two_requests_for_one_challenge_file_it_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    gate = threading.Barrier(2)
    results: list[bool] = []

    def fetch(url: str) -> Fetched:
        gate.wait(5)
        return _page()

    def run() -> None:
        results.append(store.file(_submission(), fetch)[1])

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert sorted(results) == [False, True] and len(store.records()) == 1


@pytest.mark.parametrize("quote", ["Sentence with an unassigned character ͸ inside", "Sentence with a lone surrogate \ud800 inside"])
def test_text_the_record_cannot_hold_is_refused_as_a_bad_submission(tmp_path: Path, quote: str) -> None:
    store = _store(tmp_path)
    with pytest.raises(cs.ChallengeError):
        store.file(_submission(quote=quote), lambda u: _page())
    assert store.records() == []


def test_a_torn_line_is_not_counted_as_the_record_before_it(tmp_path: Path) -> None:
    """The count a full store is judged by never gains a record from a line that is not one."""
    store = _store(tmp_path)
    store.file(_submission(), lambda u: _page())
    with (tmp_path / "c.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"id":"chTORN","rec\n')
    other = _submission(quote="Another sentence copied from the source page")
    previous = cs.MAX_RECORDS
    cs.MAX_RECORDS = 2  # one record and a torn line: there is room for one more
    try:
        row, new = store.file(other, lambda u: _page("Another sentence copied from the source page"))
    finally:
        cs.MAX_RECORDS = previous
    assert new and row["state"] == "QUOTE_FOUND"


def test_a_file_that_is_one_torn_character_still_takes_a_record(tmp_path: Path) -> None:
    (tmp_path / "c.jsonl").write_text("{", encoding="utf-8")
    store = _store(tmp_path)
    row, new = store.file(_submission(), lambda u: _page())
    assert new and [r["id"] for r in store.records()] == [row["id"]]


def _held_for(path: Path, seconds: float):  # type: ignore[no-untyped-def]
    """Take the lock of the store's file now and give it back after ``seconds``; returns what to call to take it (before the work)."""
    import fcntl

    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    threading.Timer(seconds, lambda: (fcntl.flock(handle, fcntl.LOCK_UN), handle.close())).start()


def test_looking_for_a_record_waits_for_the_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.file(_submission(), lambda u: _page())
    _held_for(tmp_path / "c.jsonl", 0.6)
    started = time.monotonic()
    store._known("none")
    assert time.monotonic() - started >= 0.5


def test_adding_a_record_waits_for_the_lock(tmp_path: Path) -> None:
    import fcntl

    store = _store(tmp_path)
    handle = (tmp_path / "c.jsonl").open("a+", encoding="utf-8")

    def fetch(url: str) -> Fetched:  # after the first look, before the append: the lock is taken by someone else now
        fcntl.flock(handle, fcntl.LOCK_EX)
        threading.Timer(0.6, lambda: (fcntl.flock(handle, fcntl.LOCK_UN), handle.close())).start()
        return _page()

    started = time.monotonic()
    row, new = store.file(_submission(), fetch)
    assert new and time.monotonic() - started >= 0.5
    assert [r["id"] for r in store.records()] == [row["id"]]
