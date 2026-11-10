"""Evidence challenges: the submission is checked, the source is fetched by the server, the quote is looked for, and nothing else is decided."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hoshitori import challenge_store as cs
from hoshitori.fetch_types import FetchCode, Fetched, FetchError

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
RECEIPT = "fc" + "a" * 26
URL = "https://example.org/notice"
QUOTE = "Trading in SOL will resume at 14:00 UTC"


def page(text: str, charset: str | None = "utf-8") -> Fetched:
    try:
        body = text.encode(charset or "utf-8")
    except LookupError:
        body = text.encode("utf-8")  # a charset nobody knows: the bytes are utf-8 and the server must still read them
    return Fetched(URL, 200, "text/html", charset, body, "d" * 64, (), "93.184.216.34", ())


def sub(**over: object) -> dict[str, object]:
    return {"receipt": RECEIPT, "kind": "DISPUTE_CLAIM", "url": URL, "quote": QUOTE, "note": "see the notice", **over}


def test_a_good_submission_is_read_and_normalised() -> None:
    s = cs.read_submission(sub(quote="  Trading in SOL\n will   resume at 14:00 UTC ", note=" a  note "))
    assert (s.receipt, s.kind, s.url, s.quote, s.note) == (RECEIPT, "DISPUTE_CLAIM", URL, QUOTE, "a note")
    assert cs.read_submission({k: v for k, v in sub().items() if k != "note"}).note == ""


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        sub(extra=1),
        sub(receipt="x"),
        sub(receipt=5),
        sub(kind="OTHER"),
        sub(url="http://example.org"),
        sub(url="https://exa mple.org"),
        sub(url="https://e.org/" + "a" * 500),
        sub(url=5),
        sub(quote="too short"),
        sub(quote="x" * 401),
        sub(quote=5),
        sub(note="n" * 501),
        sub(note=5),
        sub(note="a\x00b"),
        sub(quote="Trading in SOL will resume \x07 at 14:00"),
    ],
)
def test_a_submission_that_is_not_one_is_refused_without_repeating_it(bad: object) -> None:
    with pytest.raises(cs.ChallengeError) as caught:
        cs.read_submission(bad)
    assert "example.org" not in str(caught.value) and "Trading" not in str(caught.value)


def test_the_limits_are_inclusive() -> None:
    assert cs.read_submission(sub(quote="x" * 12)).quote == "x" * 12 and cs.read_submission(sub(quote="x" * 400)).quote == "x" * 400
    assert cs.read_submission(sub(note="n" * 500)).note == "n" * 500
    assert cs.read_submission(sub(url="https://e.org/" + "a" * 486)).url.endswith("a")
    assert (
        cs.KINDS == ("ADD_SOURCE", "DISPUTE_CLAIM", "CORRECT_FACT")
        and cs.MAX_RECORDS == 5000
        and cs.STATES == ("QUOTE_FOUND", "QUOTE_NOT_FOUND", "SOURCE_UNAVAILABLE")
    )


def test_a_quote_in_the_fetched_page_is_found_through_tags_entities_case_and_spaces() -> None:
    s = cs.read_submission(sub())
    html = "<html><head><style>a{}</style><script>var x='nope';</script></head><body><p>Trading   in <b>SOL</b> will resume at 14:00&nbsp;UTC.</p></body></html>"
    got = cs.triage(s, lambda u: page(html))
    assert got == {"state": "QUOTE_FOUND", "origin": "ORIGIN_FETCHED_BY_US", "reason": None, "source_sha256": "d" * 64}
    assert cs.triage(s, lambda u: page("TRADING IN SOL WILL RESUME AT 14:00 UTC"))["state"] == "QUOTE_FOUND"
    assert cs.triage(s, lambda u: page("<p>Trading in ETH will resume at 14:00 UTC</p>"))["state"] == "QUOTE_NOT_FOUND"


def test_words_hidden_in_a_script_or_a_style_do_not_count() -> None:
    s = cs.read_submission(sub())
    assert cs.triage(s, lambda u: page(f"<script>{QUOTE}</script>"))["state"] == "QUOTE_NOT_FOUND"
    assert cs.triage(s, lambda u: page(f"<style>{QUOTE}</style>"))["state"] == "QUOTE_NOT_FOUND"


def test_the_page_is_read_in_its_charset_and_an_unknown_one_falls_back() -> None:
    s = cs.read_submission(sub(quote="Café opens trading in SOL at noon"))
    assert cs.triage(s, lambda u: page("Café opens trading in SOL at noon", "latin-1"))["state"] == "QUOTE_FOUND"
    assert cs.triage(s, lambda u: page("Café opens trading in SOL at noon", "no-such-charset"))["state"] == "QUOTE_FOUND"


def test_a_source_that_cannot_be_fetched_stays_asserted_with_the_reason() -> None:
    def fail(url: str) -> Fetched:
        raise FetchError(FetchCode.ADDRESS_NOT_PUBLIC, "x")

    assert cs.triage(cs.read_submission(sub()), fail) == {
        "state": "SOURCE_UNAVAILABLE",
        "origin": "SUBMITTER_ASSERTED",
        "reason": "ADDRESS_NOT_PUBLIC",
        "source_sha256": None,
    }


def test_the_same_submission_is_one_challenge_and_fetches_nothing_the_second_time(tmp_path: Path) -> None:
    store = cs.ChallengeStore(tmp_path / "c.jsonl", now=lambda: NOW)
    calls: list[str] = []

    def fetch(url: str) -> Fetched:
        calls.append(url)
        return page(QUOTE)

    first, new = store.file(cs.read_submission(sub()), fetch)
    again, new_again = store.file(cs.read_submission(sub(quote=QUOTE.upper(), note="another note")), fetch)
    assert (new, new_again, again["id"] == first["id"], calls) == (True, False, True, [URL])
    assert first["received_at"] == "2026-11-04T12:00:00Z" and first["state"] == "QUOTE_FOUND" and again == first
    other, new_other = store.file(cs.read_submission(sub(url="https://example.org/other")), fetch)
    assert new_other and other["id"] != first["id"] and len(store.records()) == 2


def test_the_record_holds_what_was_said_and_nothing_about_who(tmp_path: Path) -> None:
    store = cs.ChallengeStore(tmp_path / "c.jsonl", now=lambda: NOW)
    row, _ = store.file(cs.read_submission(sub()), lambda u: page(QUOTE))
    assert set(row) == {"id", "received_at", "receipt", "kind", "url", "quote", "note", "state", "origin", "reason", "source_sha256"}
    assert row["id"].startswith("ch") and len(row["id"]) == 22
    text = (tmp_path / "c.jsonl").read_text()
    assert text.count("\n") == 1 and json.loads(text) == row


def test_the_records_of_one_receipt_and_a_torn_line_is_not_a_record(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = cs.ChallengeStore(path, now=lambda: NOW)
    store.file(cs.read_submission(sub()), lambda u: page(QUOTE))
    store.file(cs.read_submission(sub(receipt="fc" + "b" * 26)), lambda u: page("nothing"))
    with path.open("a") as h:
        h.write('{"id": "torn", "state": "QUOTE_FO')
    assert [r["receipt"] for r in store.records()] == [RECEIPT, "fc" + "b" * 26]
    assert [r["state"] for r in store.for_receipt("fc" + "b" * 26)] == ["QUOTE_NOT_FOUND"] and store.for_receipt("fc" + "c" * 26) == []


def test_the_file_is_full_at_its_limit_and_says_so(tmp_path: Path) -> None:
    kept = cs.MAX_RECORDS
    cs.MAX_RECORDS = 2
    try:
        store = cs.ChallengeStore(tmp_path / "c.jsonl", now=lambda: NOW)
        for n in range(2):
            store.file(cs.read_submission(sub(url=f"https://example.org/{n}")), lambda u: page(QUOTE))
        with pytest.raises(cs.ChallengeError, match="as many challenges"):
            store.file(cs.read_submission(sub(url="https://example.org/3")), lambda u: page(QUOTE))
        again, new = store.file(cs.read_submission(sub(url="https://example.org/0")), lambda u: page(QUOTE))
        assert not new and again["url"].endswith("/0")
    finally:
        cs.MAX_RECORDS = kept


def test_two_people_filing_the_same_thing_at_once_make_one_record(tmp_path: Path) -> None:
    store = cs.ChallengeStore(tmp_path / "c.jsonl", now=lambda: NOW)
    results: list[bool] = []

    def slow(url: str) -> Fetched:
        time.sleep(0.05)  # the window in which a second filer could also find nothing there
        return page(QUOTE)

    def go() -> None:
        results.append(store.file(cs.read_submission(sub()), slow)[1])

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 7 + [True] and len(store.records()) == 1


def test_the_store_makes_its_folder_and_file(tmp_path: Path) -> None:
    store = cs.ChallengeStore(tmp_path / "deep" / "er" / "c.jsonl")
    assert (tmp_path / "deep" / "er" / "c.jsonl").exists() and store.records() == []


def test_an_old_file_is_kept_when_the_store_opens_again(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    first = cs.ChallengeStore(path, now=lambda: NOW)
    first.file(cs.read_submission(sub()), lambda u: page(QUOTE))
    assert len(cs.ChallengeStore(path).records()) == 1


def test_lines_that_are_not_records_are_not_read(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = cs.ChallengeStore(path, now=lambda: NOW)
    path.write_text('[1]\n{"state": "OTHER", "receipt": "x"}\n"text"\n{"state": "QUOTE_FOUND", "receipt": "r"}\n')
    assert store.records() == [{"state": "QUOTE_FOUND", "receipt": "r"}]


def test_a_page_with_bytes_that_are_not_its_charset_is_still_read(tmp_path: Path) -> None:
    s = cs.read_submission(sub())
    broken = Fetched(URL, 200, "text/html", "utf-8", b"\xff\xfe " + QUOTE.encode() + b" \xff", "d" * 64, (), "1.1.1.1", ())
    assert cs.triage(s, lambda u: broken)["state"] == "QUOTE_FOUND"
    unknown = Fetched(URL, 200, "text/html", "no-such-charset", b"\xff " + QUOTE.encode(), "d" * 64, (), "1.1.1.1", ())
    assert cs.triage(s, lambda u: unknown)["state"] == "QUOTE_FOUND"


def test_the_characters_of_an_address_and_the_lengths_are_exact() -> None:
    cs.read_submission(sub(url="https://e.org/!~"))  # the first and the last plain characters are allowed
    for bad in ("https://e.org/ x", "https://e.org/\x7f", "https://e.org/\x1f"):
        with pytest.raises(cs.ChallengeError):
            cs.read_submission(sub(url=bad))
    assert cs.read_submission(sub(url="https://e.org/" + "a" * 486)).url.endswith("a")
    with pytest.raises(cs.ChallengeError):
        cs.read_submission(sub(url="https://e.org/" + "a" * 487))
    with pytest.raises(cs.ChallengeError):
        cs.read_submission(sub(note="n" * 501))
    assert cs.read_submission(sub(note="line one\nline two\tend")).note == "line one line two end"


def test_a_submission_is_frozen() -> None:
    s = cs.read_submission(sub())
    with pytest.raises(AttributeError):
        s.url = "https://x.org"  # type: ignore[misc]
