"""Raw captures: every external response, stored as received before anything reads it (``DATA_PROVENANCE`` principle 1).

A ``RawCapture`` is the bytes of one fetch with its status, secret-free headers, timing and the provider's trace id. Two rules
carry the weight (contract section 18):

* **Every fetch is its own observation.** The capture id is assigned before the request is sent and names the fetch, never the
  content. The SHA-256 of the body is an attribute: identical bytes fetched twice are two captures that share one stored blob.
  Keying evidence on the content hash dropped or rejected every repeated identical response.
* **The store is append-only and write-once.** The blob is written (to a temporary file, fsynced, renamed, directory fsynced)
  before the metadata line that names it, and each line is fsynced before the next is written, so a crash leaves at most a torn
  last line or an orphan blob, never a line without its bytes. Re-appending the same capture is a no-op; appending a different
  capture under an existing id is a conflict.

Layout::

    <root>/LOCK                       advisory lock; one writer
    <root>/captures.jsonl             one canonical JSON object per line, hash-chained (``prev``)
    <root>/blobs/<aa>/<sha256>        the bodies, content-addressed

Headers and URLs are scrubbed of anything that looks like a credential before they are stored, and a capture that still carries
one is refused (a stored secret is a disqualifying defect, so the check is in the type).
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from hoshitori import canonical
from hoshitori.timeutil import ensure_utc, format_rfc3339_ms, parse_rfc3339

CAPTURE_ID: Final = re.compile(r"cap-[0-9a-f]{32}")
SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
GENESIS: Final = "0" * 64
SECRET_NAME_PARTS: Final = ("auth", "key", "token", "secret", "cookie", "sign", "password", "passphrase", "credential", "session")
REDACTED: Final = "REDACTED"
MAX_BODY_BYTES: Final = 64 * 1024 * 1024
MAX_HEADER_BYTES: Final = 16 * 1024
MAX_LINE_BYTES: Final = 256 * 1024


class CaptureError(ValueError):
    """A capture is malformed or carries something that must never be stored."""


class CaptureStoreError(Exception):
    """The store cannot do what was asked."""


class CaptureDamage(CaptureStoreError):
    """The files are not what was written: a broken chain, an invalid line before the end, a missing or altered blob."""


class CaptureConflict(CaptureStoreError):
    """A different capture was appended under an id that is already used."""


class StoreLocked(CaptureStoreError):
    """Another writer holds the store."""


def new_capture_id() -> str:
    return "cap-" + secrets.token_hex(16)


def looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Lower-cased headers without any whose name looks like a credential."""
    return {name.lower(): value for name, value in headers.items() if not looks_secret(name)}


def redact_url(url: str) -> str:
    """The URL without user information and with the value of every credential-looking query parameter replaced."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port is not None else "")
    query = urlencode([(k, REDACTED if looks_secret(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


@dataclass(frozen=True)
class RawCapture:
    capture_id: str
    source: str
    request: Mapping[str, canonical.JsonValue]
    status: int | None
    headers: Mapping[str, str]
    body: bytes
    t_send: datetime
    received_at: datetime
    trace_id: str | None = None
    body_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not CAPTURE_ID.fullmatch(self.capture_id):
            raise CaptureError(f"bad capture id {self.capture_id!r}")
        if not self.source or not self.source.isascii() or not re.fullmatch(r"[a-z0-9_.:-]{1,64}", self.source):
            raise CaptureError(f"bad source {self.source!r}")
        if self.status is not None and not (100 <= self.status <= 599):
            raise CaptureError(f"bad HTTP status {self.status!r}")
        if not isinstance(self.body, bytes):
            raise CaptureError("the body must be bytes")
        if len(self.body) > MAX_BODY_BYTES:
            raise CaptureError("body too large")
        object.__setattr__(self, "t_send", ensure_utc(self.t_send))
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        if self.received_at < self.t_send:
            raise CaptureError("received before it was sent")
        for name, value in self.headers.items():
            if name != name.lower() or not name or not isinstance(value, str):
                raise CaptureError(f"header {name!r} must be a lower-case name with a text value")
            if looks_secret(name):
                raise CaptureError(f"header {name!r} looks like a credential and is never stored")
        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:
            raise CaptureError("headers too large")
        if self.trace_id is not None and not (isinstance(self.trace_id, str) and 0 < len(self.trace_id) <= 128):
            raise CaptureError("bad trace id")
        url = self.request.get("url")
        if isinstance(url, str):
            parts = urlsplit(url)
            if parts.username is not None or parts.password is not None:
                raise CaptureError("the request URL carries user information")
            if any(looks_secret(k) and v != REDACTED for k, v in parse_qsl(parts.query, keep_blank_values=True)):
                raise CaptureError("the request URL carries a credential-looking query value")
        object.__setattr__(self, "body_sha256", hashlib.sha256(self.body).hexdigest())

    def elapsed_s(self) -> float:
        return (self.received_at - self.t_send).total_seconds()


@dataclass(frozen=True)
class Stored:
    """Where a capture is and what commits to it: the evidence layer records ``line_sha256`` so a later edit of the metadata shows."""

    seq: int
    capture_id: str
    body_sha256: str
    line_sha256: str


@dataclass
class Recovery:
    """What opening the store had to repair."""

    captures: int = 0
    truncated_bytes: int = 0
    orphan_blobs: int = 0
    removed_temporaries: int = 0


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


class CaptureStore:
    """The append-only store. One writer per directory; ``crash`` is a test hook called at each durable step.

    ``read_only`` opens it for reading beside a running writer: no lock is taken, no folder is made, a torn tail and the temporary files of
    a body being written are left alone (they are the writer's), and nothing can be appended. A line the writer is part-way through is not
    a capture. A store that is not there is an empty one."""

    def __init__(self, root: Path, *, crash: Callable[[str], None] | None = None, read_only: bool = False) -> None:
        self.root = root
        self._crash = crash
        self._read_only = read_only
        self._lock_fd: int | None = None
        self._index: dict[str, dict[str, canonical.JsonValue]] = {}
        self._stored: dict[str, Stored] = {}
        self._order: list[str] = []
        self._last_hash = GENESIS
        self.recovery = Recovery()
        self._open()

    # --- opening and recovery ------------------------------------------------------------------------------------------
    def _hook(self, point: str) -> None:
        if self._crash is not None:
            self._crash(point)

    def _open(self) -> None:
        if self._read_only:
            self._scan()
            return
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(self.root / "LOCK", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise StoreLocked(f"another writer holds {self.root}") from exc
        try:
            self._scan()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> CaptureStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def _log(self) -> Path:
        return self.root / "captures.jsonl"

    def _blob_path(self, sha: str) -> Path:
        return self.root / "blobs" / sha[:2] / sha

    def _scan(self) -> None:
        data = self._log.read_bytes() if self._log.exists() else b""
        end = data.rfind(b"\n") + 1  # everything after the last newline is a torn tail
        complete, tail = data[:end], data[end:]
        lines = complete.split(b"\n")[:-1] if complete else []
        prev = GENESIS
        offset = 0
        for number, text in enumerate(lines):
            line = text + b"\n"
            final = number == len(lines) - 1
            try:
                meta = self._check_line(line, number, prev)
            except CaptureDamage:
                if final and not tail:
                    # a complete final line that does not verify: power-loss style tearing can leave one; it was never acknowledged
                    # unless its blob and chain verify, so it is removed like a torn tail
                    tail = line
                    complete = complete[:offset]
                    break
                raise
            self._index[str(meta["capture_id"])] = meta
            self._order.append(str(meta["capture_id"]))
            prev = _line_hash(line)
            self._stored[str(meta["capture_id"])] = Stored(number, str(meta["capture_id"]), str(meta["body_sha256"]), prev)
            offset += len(line)
        self._last_hash = prev
        if tail and not self._read_only:
            with open(self._log, "r+b") as handle:
                handle.truncate(len(complete))
                handle.flush()
                os.fsync(handle.fileno())
            self.recovery.truncated_bytes = len(tail)
        self.recovery.captures = len(self._order)
        if not self._read_only:
            for stale in (self.root / "blobs").rglob("*.tmp"):  # a body that was being written when the process died; we hold the lock
                stale.unlink()
                self.recovery.removed_temporaries += 1
        referenced = {str(meta["body_sha256"]) for meta in self._index.values()}
        for meta in self._index.values():
            path = self._blob_path(str(meta["body_sha256"]))
            if not path.is_file():
                raise CaptureDamage(f"the body {meta['body_sha256']} of a stored capture is missing")
            if path.stat().st_size != meta["body_len"]:
                raise CaptureDamage(f"the body {meta['body_sha256']} has {path.stat().st_size} bytes, the capture says {meta['body_len']}")
        self.recovery.orphan_blobs = sum(1 for _ in self._orphans(referenced))

    META_KEYS: Final = frozenset(
        {
            "body_len",
            "body_sha256",
            "capture_id",
            "headers",
            "prev",
            "received_at",
            "request",
            "seq",
            "source",
            "status",
            "t_send",
            "trace_id",
        }
    )

    def _check_line(self, line: bytes, number: int, prev: str) -> dict[str, canonical.JsonValue]:
        if len(line) > MAX_LINE_BYTES:
            raise CaptureDamage(f"line {number} is too long")
        try:
            meta = canonical.parse_record(line[:-1])
        except canonical.CanonicalError as exc:
            raise CaptureDamage(f"line {number} is not a canonical record: {exc}") from exc
        if canonical.dumps(meta) != line[:-1]:
            raise CaptureDamage(f"line {number} is not in canonical form")
        if set(meta) != self.META_KEYS:
            raise CaptureDamage(f"line {number} has the members {sorted(meta)}")
        capture_id, sha, headers, request = meta["capture_id"], meta["body_sha256"], meta["headers"], meta["request"]
        if not (isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id)):
            raise CaptureDamage(f"line {number} has a bad capture id")
        if not (isinstance(sha, str) and SHA256_HEX.fullmatch(sha)):
            raise CaptureDamage(f"line {number} has a bad body hash")
        status, trace, length = meta["status"], meta["trace_id"], meta["body_len"]
        if not (isinstance(length, int) and not isinstance(length, bool) and 0 <= length <= MAX_BODY_BYTES):
            raise CaptureDamage(f"line {number} has a bad body length")
        if not (isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values()) and isinstance(request, dict)):
            raise CaptureDamage(f"line {number} has bad headers or request")
        if not (status is None or (isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599)):
            raise CaptureDamage(f"line {number} has a bad status")
        if not (trace is None or isinstance(trace, str)) or not isinstance(meta["source"], str):
            raise CaptureDamage(f"line {number} has a bad trace id or source")
        try:
            parse_rfc3339(str(meta["t_send"]))
            parse_rfc3339(str(meta["received_at"]))
        except ValueError as exc:
            raise CaptureDamage(f"line {number} has a bad time") from exc
        if meta["seq"] != number or isinstance(meta["seq"], bool):
            raise CaptureDamage(f"line {number} carries sequence number {meta['seq']!r}")
        if meta["prev"] != prev:
            raise CaptureDamage(f"line {number} does not follow the line before it")
        if capture_id in self._index:
            raise CaptureDamage(f"capture id {capture_id} appears twice")
        return meta

    def _orphans(self, referenced: set[str]) -> Iterator[Path]:
        blobs = self.root / "blobs"
        if not blobs.is_dir():
            return
        for sub in blobs.iterdir():
            if not sub.is_dir():
                continue
            for path in sub.iterdir():
                if SHA256_HEX.fullmatch(path.name) and path.name not in referenced:
                    yield path

    # --- writing -------------------------------------------------------------------------------------------------------
    def _write_blob(self, sha: str, body: bytes) -> None:
        path = self._blob_path(sha)
        if path.is_file() and path.stat().st_size == len(body):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{sha}.{secrets.token_hex(4)}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        self._hook("blob-written")
        os.replace(tmp, path)
        _fsync_dir(path.parent)

    def append(self, capture: RawCapture) -> Stored:
        """Store ``capture``. Appending the same capture again returns the same ``Stored``; a different one under that id is a conflict."""
        if self._read_only:
            raise CaptureStoreError("the store is read-only")
        if self._lock_fd is None:
            raise CaptureStoreError("the store is closed")
        meta = self._meta(capture)
        known = self._index.get(capture.capture_id)
        if known is not None:
            if {k: v for k, v in known.items() if k not in ("seq", "prev")} != {k: v for k, v in meta.items() if k not in ("seq", "prev")}:
                raise CaptureConflict(f"{capture.capture_id} was stored with different content")
            return self._stored[capture.capture_id]
        seq = len(self._order)
        meta["seq"] = seq
        meta["prev"] = self._last_hash
        line = canonical.dumps(meta) + b"\n"
        if len(line) > MAX_LINE_BYTES:
            raise CaptureError("the capture's metadata is too large")
        self._write_blob(capture.body_sha256, capture.body)
        self._hook("blob-durable")
        with open(self._log, "ab") as handle:
            handle.write(line)
            self._hook("line-written")
            handle.flush()
            os.fsync(handle.fileno())
        if seq == 0:
            _fsync_dir(self.root)
        self._hook("line-durable")
        self._index[capture.capture_id] = meta
        self._order.append(capture.capture_id)
        self._last_hash = _line_hash(line)
        stored = Stored(seq, capture.capture_id, capture.body_sha256, self._last_hash)
        self._stored[capture.capture_id] = stored
        return stored

    @staticmethod
    def _meta(capture: RawCapture) -> dict[str, canonical.JsonValue]:
        return {
            "body_len": len(capture.body),
            "body_sha256": capture.body_sha256,
            "capture_id": capture.capture_id,
            "headers": dict(capture.headers),
            "received_at": format_rfc3339_ms(capture.received_at),
            "request": dict(capture.request),
            "source": capture.source,
            "status": capture.status,
            "t_send": format_rfc3339_ms(capture.t_send),
            "trace_id": capture.trace_id,
        }

    # --- reading -------------------------------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._order)

    def ids(self) -> list[str]:
        return list(self._order)

    def count_since(self, source: str, since: datetime) -> int:
        """How many captures of ``source`` were sent at or after ``since``: the calls a provider has been asked for in a window, retries
        included, since every attempt that reached it left a capture."""
        start = ensure_utc(since)
        return sum(1 for meta in self._index.values() if meta["source"] == source and parse_rfc3339(str(meta["t_send"])) >= start)

    def __contains__(self, capture_id: object) -> bool:
        return capture_id in self._index

    def stored(self, capture_id: str) -> Stored:
        return self._stored[capture_id]

    def body(self, sha256: str) -> bytes:
        """The stored bytes for ``sha256``, re-hashed on every read."""
        if not SHA256_HEX.fullmatch(sha256):
            raise CaptureStoreError("not a SHA-256 value")
        try:
            data = self._blob_path(sha256).read_bytes()
        except FileNotFoundError as exc:
            raise CaptureDamage(f"the body {sha256} is missing") from exc
        if hashlib.sha256(data).hexdigest() != sha256:
            raise CaptureDamage(f"the body {sha256} has been altered")
        return data

    def get(self, capture_id: str) -> RawCapture:
        meta = self._index.get(capture_id)
        if meta is None:
            raise KeyError(capture_id)
        body = self.body(str(meta["body_sha256"]))
        headers = meta["headers"]
        request = meta["request"]
        status = meta["status"]
        trace = meta["trace_id"]
        assert isinstance(headers, dict) and isinstance(request, dict)
        return RawCapture(
            capture_id=str(meta["capture_id"]),
            source=str(meta["source"]),
            request=request,
            status=status if isinstance(status, int) else None,
            headers={str(k): str(v) for k, v in headers.items()},
            body=body,
            t_send=parse_rfc3339(str(meta["t_send"])),
            received_at=parse_rfc3339(str(meta["received_at"])),
            trace_id=trace if isinstance(trace, str) else None,
        )

    def blob_count(self) -> int:
        return sum(1 for sub in (self.root / "blobs").iterdir() if sub.is_dir() for p in sub.iterdir() if SHA256_HEX.fullmatch(p.name))
