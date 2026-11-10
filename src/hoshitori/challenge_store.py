"""Evidence challenges from people: a structured submission, checked by the server against the source it cites, kept in an append-
only file.

A person who thinks a call is wrong, or missing something, files a challenge against its receipt: the kind of challenge, a
source address, the words they quote from it and an optional note (contract section 32.1). The server does what a machine can:
it fetches the address through the safe fetch client (https only, no private address, bounded) and looks for the quoted words in
what came back. The record then carries one of three states and says who fetched it:

* ``QUOTE_FOUND``: the quoted words are in the page the server itself fetched (origin class ``ORIGIN_FETCHED_BY_US``);
* ``QUOTE_NOT_FOUND``: the page was fetched and does not hold them;
* ``SOURCE_UNAVAILABLE``: the page could not be fetched (the reason is the fetch client's closed code), so the submission stays
  ``SUBMITTER_ASSERTED``.

Nothing else is decided here. A challenge earns no credit, changes no forecast and is not a verdict on anyone's reasoning:
whether it corrects a fact is for a reviewer (section 32.1a), and no number of people agreeing counts. The same submission twice
is one record. The file holds at most ``MAX_RECORDS`` records and refuses more rather than grow without bound. The submitter's
address is never written.
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from hoshitori import canonical
from hoshitori.fetch_types import Fetched, FetchError

KINDS: Final = ("ADD_SOURCE", "DISPUTE_CLAIM", "CORRECT_FACT")
RECEIPT: Final = re.compile(r"fc[a-z2-7]{26,60}")
MAX_URL: Final = 500
MAX_QUOTE: Final = 400
MIN_QUOTE: Final = 12
MAX_NOTE: Final = 500
MAX_RECORDS: Final = 5000
MAX_BLOCK: Final = 100000  # the longest script or style block taken out whole
MAX_TAG: Final = 2000  # the longest tag taken out
_OPENER: Final = re.compile(r"<(?:script|style)\b", re.IGNORECASE)


def _strip_tags(text: str) -> str:
    """The text with script and style blocks and tags replaced by a space, in one pass over the text.

    It does what the pattern ``<(script|style)\\b.{0,N}?</(script|style)>|<[^>]{0,M}>`` did, but cannot be made quadratic by a page
    full of unclosed openers: the position of the next closer and the next ``>`` is found once and only moves forward.
    """
    lowered = text.lower()
    out: list[str] = []
    i = last = 0
    close_at: dict[str, int] = {}
    gt = -2
    n = len(text)
    while True:
        i = text.find("<", i)
        if i < 0:
            break
        end = -1
        opener = _OPENER.match(text, i)
        if opener:
            best = -1
            for closer in ("</script>", "</style>"):
                at = close_at.get(closer, -2)
                if (at != -1 and at < i) or at == -2:
                    at = lowered.find(closer, i)
                    close_at[closer] = at
                if at != -1 and (best == -1 or at < best):
                    best = at
            # the lazy block ends at the first closer after the opener word; at most MAX_BLOCK characters may lie between them
            if best != -1 and best - opener.end() <= MAX_BLOCK:
                closer_len = 9 if lowered.startswith("</script>", best) else 8
                end = best + closer_len
        if end < 0:
            if gt < i + 1 and gt != -1:
                gt = text.find(">", i + 1)
            if gt != -1 and gt - i - 1 <= MAX_TAG:
                end = gt + 1
        if end < 0:
            i += 1
            continue
        out.append(text[last:i])
        out.append(" ")
        last = i = end
    out.append(text[last:n])
    return "".join(out)


_SPACE: Final = re.compile(r"\s+")
STATES: Final = ("QUOTE_FOUND", "QUOTE_NOT_FOUND", "SOURCE_UNAVAILABLE")


class ChallengeError(ValueError):
    """A submission that is not what this reads; the message is for the person and never repeats what they sent."""


@dataclass(frozen=True)
class Submission:
    receipt: str
    kind: str
    url: str
    quote: str
    note: str


def _plain(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def read_submission(body: object) -> Submission:
    """The submission in a request body, checked field by field."""
    if not isinstance(body, dict) or set(body) - {"receipt", "kind", "url", "quote", "note"}:
        raise ChallengeError("send a receipt, a kind, a source address, a quote and optionally a note")
    receipt, kind, url, quote, note = (body.get(k, "" if k == "note" else None) for k in ("receipt", "kind", "url", "quote", "note"))
    if not isinstance(receipt, str) or RECEIPT.fullmatch(receipt) is None:
        raise ChallengeError("the receipt is the identifier of a call")
    if kind not in KINDS:
        raise ChallengeError(f"the kind is one of {', '.join(KINDS)}")
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > MAX_URL or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ChallengeError(f"the source is an https address of at most {MAX_URL} plain characters")
    if not isinstance(quote, str) or not MIN_QUOTE <= len(_plain(quote)) <= MAX_QUOTE:
        raise ChallengeError(f"the quote is {MIN_QUOTE} to {MAX_QUOTE} characters, copied from the source")
    if not isinstance(note, str) or len(note) > MAX_NOTE:
        raise ChallengeError(f"the note is at most {MAX_NOTE} characters")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in quote + note):
        raise ChallengeError("the quote and the note are text, with no control characters")
    return Submission(receipt, kind, url, _plain(quote), _plain(note))


def triage(submission: Submission, fetch: Callable[[str], Fetched]) -> dict[str, Any]:
    """Fetch the source and look for the quote. The answer says what happened and who fetched what; it never says the quote is true."""
    try:
        got = fetch(submission.url)
    except FetchError as exc:
        return {"state": "SOURCE_UNAVAILABLE", "origin": "SUBMITTER_ASSERTED", "reason": exc.code.name, "source_sha256": None}
    try:
        text = got.body.decode(got.charset or "utf-8", "replace")
    except LookupError:
        text = got.body.decode("utf-8", "replace")
    page = _plain(html.unescape(_strip_tags(text)))
    found = submission.quote.casefold() in page.casefold()
    return {
        "state": "QUOTE_FOUND" if found else "QUOTE_NOT_FOUND",
        "origin": "ORIGIN_FETCHED_BY_US",
        "reason": None,
        "source_sha256": got.sha256,
    }


def record_id(s: Submission) -> str:
    """One name for one submission: the same receipt, source and quote are the same challenge."""
    return "ch" + hashlib.sha256(f"{s.receipt}\n{s.url}\n{s.quote.casefold()}".encode()).hexdigest()[:20]


class ChallengeStore:
    """An append-only file of challenges, one canonical line each, under an exclusive lock for every append."""

    def __init__(self, path: Path, now: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._now = now or (lambda: datetime.now(UTC))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn last line is not a record
            if isinstance(row, dict) and row.get("state") in STATES:
                out.append(row)
        return out

    def for_receipt(self, receipt: str) -> list[dict[str, Any]]:
        return [r for r in self.records() if r["receipt"] == receipt]

    def _rows(self, handle: Any) -> list[dict[str, Any]]:
        """The records in the open file; a line that is not a record (a torn last line) is skipped, never raised on."""
        handle.seek(0)
        rows: list[dict[str, Any]] = []
        for line in handle.read().splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _known(self, ident: str) -> dict[str, Any] | None:
        """The record already filed under this id, or none; raises when the store is full."""
        with self._path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                rows = self._rows(handle)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        for row in rows:
            if row.get("id") == ident:
                return row
        if len(rows) >= MAX_RECORDS:
            raise ChallengeError("this server is holding as many challenges as it keeps; try again later")
        return None

    def file(self, s: Submission, fetch: Callable[[str], Fetched]) -> tuple[dict[str, Any], bool]:
        """The record of the challenge and whether it is new. The same submission again returns the first record and fetches nothing.

        The source is fetched with no lock held, so a slow source delays nobody else; the lock is taken to look and again to append."""
        try:
            ident = record_id(s)
        except UnicodeError as exc:  # a lone surrogate in the text
            raise ChallengeError("the quote and the note are text the record cannot hold") from exc
        known = self._known(ident)
        if known is not None:
            return known, False
        result = triage(s, fetch)
        row = {
            "id": ident,
            "received_at": self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "receipt": s.receipt,
            "kind": s.kind,
            "url": s.url,
            "quote": s.quote,
            "note": s.note,
            **result,
        }
        try:
            line = canonical.dumps(row).decode("utf-8") + "\n"
        except (ValueError, UnicodeError) as exc:  # text the canonical form refuses (an unassigned character, a lone surrogate)
            raise ChallengeError("the quote and the note are text the record cannot hold") from exc
        with self._path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                for other in self._rows(handle):
                    if other.get("id") == ident:  # another request filed the same challenge while this one fetched
                        return other, False
                handle.seek(0)
                body = handle.read()
                handle.seek(0, 2)
                if body and not body.endswith("\n"):  # a torn last line: end it so this record starts on its own line
                    handle.write("\n")
                handle.write(line)
                handle.flush()
                return row, True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
