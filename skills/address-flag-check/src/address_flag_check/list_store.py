"""The local copy of OFAC's SDN list (D3 5 and 7.2).

* The copy is OFAC's own ZIP, downloaded from OFAC, parsed once, and kept as a small JSON index in the store folder.
* At most once an hour a call confirms that the copy is still OFAC's newest file with a one-byte ``Range`` request; Last-Modified, ETag and
  the total size from Content-Range must all match. Only when one differs is the file (about 5.7 MB) downloaded again.
* OFAC answers with a redirect to a pre-signed link. Exactly one redirect is followed, and only to ``https://*.s3.us-gov-west-1.amazonaws.com``;
  the signed link (it carries a temporary token) is never recorded or stored.
* The time since the last successful confirmation is what makes a copy stale; OFAC's DateOfIssue only changes when OFAC publishes.
* The store is written atomically (a temporary file, then a rename), so a crash never leaves half a copy.
* Confirming the list never sends the queried address anywhere.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .errors import SourceError
from .ofac_index import ListFormatError, ListIndex, from_json, parse_zip, to_json
from .transport import MIN_LEFT_S, Caller, HttpReply

DEFAULT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP"
REDIRECT_SUFFIX = ".s3.us-gov-west-1.amazonaws.com"
REDIRECTS = (301, 302, 303, 307, 308)
CONFIRM_EVERY_S = 3600
STORE_FILE = "ofac_sdn_index.json"
STORE_VERSION = 1
SOURCE = "ofac_sdn"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CONTENT_RANGE = re.compile(r"bytes \d+-\d+/(\d+)")


@dataclass(frozen=True)
class StoredList:
    index: ListIndex
    zip_sha256: str
    zip_bytes: int
    last_modified: str | None
    etag: str | None
    downloaded_at: str
    confirmed_at: str


@dataclass(frozen=True)
class ListState:
    stored: StoredList | None
    action: str  # recent | confirmed | downloaded | kept | none
    error: SourceError | None


def default_dir(env: Mapping[str, str] = os.environ) -> pathlib.Path:
    """``$ADDRESS_FLAG_CHECK_HOME``, else ``$XDG_CACHE_HOME/address-flag-check``, else ``~/.cache/address-flag-check``."""
    explicit = env.get("ADDRESS_FLAG_CHECK_HOME")
    if explicit:
        return pathlib.Path(explicit)
    cache = env.get("XDG_CACHE_HOME")
    base = pathlib.Path(cache) if cache else pathlib.Path(env.get("HOME") or pathlib.Path.home()) / ".cache"
    return base / "address-flag-check"


def content_range_total(value: str | None) -> int | None:
    match = _CONTENT_RANGE.fullmatch((value or "").strip())
    return int(match.group(1)) if match else None


def stamp(t: dt.datetime) -> str:
    return t.strftime(TIME_FORMAT)


def parse_stamp(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, TIME_FORMAT).replace(tzinfo=dt.UTC)


def load(directory: pathlib.Path) -> StoredList | None:
    """The stored copy, or None when there is none or it cannot be read (a damaged copy is treated as no copy and fetched again)."""
    try:
        data = json.loads((directory / STORE_FILE).read_text(encoding="utf-8"))
        if data["format_version"] != STORE_VERSION:
            return None
        stored = StoredList(
            index=from_json(data["index"]),
            zip_sha256=str(data["zip_sha256"]),
            zip_bytes=int(data["zip_bytes"]),
            last_modified=data["last_modified"],
            etag=data["etag"],
            downloaded_at=str(data["downloaded_at"]),
            confirmed_at=str(data["confirmed_at"]),
        )
        parse_stamp(stored.confirmed_at)
        parse_stamp(stored.downloaded_at)
        return stored
    except (OSError, ValueError, KeyError, TypeError, ListFormatError):
        return None


def save(directory: pathlib.Path, stored: StoredList) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "format_version": STORE_VERSION,
        "source_url": DEFAULT_URL,
        "zip_sha256": stored.zip_sha256,
        "zip_bytes": stored.zip_bytes,
        "last_modified": stored.last_modified,
        "etag": stored.etag,
        "downloaded_at": stored.downloaded_at,
        "confirmed_at": stored.confirmed_at,
        "index": to_json(stored.index),
    }
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, directory / STORE_FILE)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _get(caller: Caller, url: str, ranged: bool) -> HttpReply:
    headers = {"Range": "bytes=0-0"} if ranged else {}
    timeout = 10.0 if ranged else 60.0
    first = caller.request(SOURCE, "GET", url, headers=headers, timeout_s=timeout)
    if first.status not in REDIRECTS:
        return first
    target = urlsplit(first.headers.get("location", ""))
    host = target.hostname or ""
    if target.scheme != "https" or not host.endswith(REDIRECT_SUFFIX):
        raise SourceError("bad_response", first.status)
    second = caller.request(SOURCE, "GET", target.geturl(), headers=headers, timeout_s=timeout, shown_url=f"https://{host}/<signed link>")
    if second.status in REDIRECTS:
        raise SourceError("bad_response", second.status)
    return second


def _same_file(reply: HttpReply, stored: StoredList) -> bool:
    return (
        reply.status == 206
        and content_range_total(reply.headers.get("content-range")) == stored.zip_bytes
        and reply.headers.get("last-modified") == stored.last_modified
        and reply.headers.get("etag") == stored.etag
    )


def ensure(
    directory: pathlib.Path, caller: Caller, now: dt.datetime, *, refresh: bool = True, url: str = DEFAULT_URL, force: bool = False
) -> ListState:
    """The copy to use for this call, refreshed when it is due and ``refresh`` allows the network; never raises for a source failure."""
    stored = load(directory)
    if stored is not None and not force:
        age = (now - parse_stamp(stored.confirmed_at)).total_seconds()
        if 0 <= age < CONFIRM_EVERY_S:
            return ListState(stored, "recent", None)
    if not refresh:
        if stored is None:
            return ListState(None, "none", SourceError("list_not_loaded"))
        return ListState(stored, "kept", None)
    try:
        reply: HttpReply | None = None
        if stored is not None and not force:
            reply = _get(caller, url, ranged=True)
            if _same_file(reply, stored):
                confirmed = replace(stored, confirmed_at=stamp(now))
                save(directory, confirmed)
                return ListState(confirmed, "confirmed", None)
            if reply.status != 200:
                reply = None
        if reply is None:
            reply = _get(caller, url, ranged=False)
        if reply.status != 200:
            raise SourceError("bad_response", reply.status)
        index = parse_zip(reply.body, should_stop=lambda: caller.remaining() <= MIN_LEFT_S + 1e-9)
        fresh = StoredList(
            index=index,
            zip_sha256=hashlib.sha256(reply.body).hexdigest(),
            zip_bytes=len(reply.body),
            last_modified=reply.headers.get("last-modified"),
            etag=reply.headers.get("etag"),
            downloaded_at=stamp(now),
            confirmed_at=stamp(now),
        )
        save(directory, fresh)
        return ListState(fresh, "downloaded", None)
    except SourceError as err:
        error = err
    except ListFormatError:
        error = SourceError("bad_response")
    except TimeoutError:
        error = SourceError("budget_exhausted")
    return ListState(stored, "none" if stored is None else "kept", error)
