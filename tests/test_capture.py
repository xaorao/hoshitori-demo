"""The raw capture store: round trips, torn tails, damage, crash points, credentials, and mutants."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshitori import capture
from tests.capture_checks import failures
from tests.mutation import mutant


def test_real_module_passes_every_check(tmp_path: Path) -> None:
    assert failures(capture, tmp_path) == []


# Redundant guards without a mutant: the explicit empty-source test (the pattern needs at least one character), and the fsync calls
# (no in-process test can see a missing fsync; the crash points are exercised through the hook), and the boolean test of a status in a
# stored line (a boolean is outside 100 to 599 anyway).
MUTATIONS: dict[str, tuple[str, str]] = {
    # identity and fields
    "a capture id may be upper case": (
        'CAPTURE_ID: Final = re.compile(r"cap-[0-9a-f]{32}")',
        'CAPTURE_ID: Final = re.compile(r"cap-[0-9a-fA-F]{32}")',
    ),
    "a capture id may be short": (
        'CAPTURE_ID: Final = re.compile(r"cap-[0-9a-f]{32}")',
        'CAPTURE_ID: Final = re.compile(r"cap-[0-9a-f]{1,32}")',
    ),
    "a source may be upper case": (
        're.fullmatch(r"[a-z0-9_.:-]{1,64}", self.source)',
        're.fullmatch(r"[a-zA-Z0-9_.: -]{1,64}", self.source)',
    ),
    "a status below 100 is accepted": (
        "if self.status is not None and not (100 <= self.status <= 599):",
        "if self.status is not None and not (0 <= self.status <= 599):",
    ),
    "a status above 599 is accepted": (
        "if self.status is not None and not (100 <= self.status <= 599):",
        "if self.status is not None and not (100 <= self.status <= 699):",
    ),
    "a status of 599 is refused": (
        "if self.status is not None and not (100 <= self.status <= 599):",
        "if self.status is not None and not (100 <= self.status <= 598):",
    ),
    "a status of 100 is refused": (
        "if self.status is not None and not (100 <= self.status <= 599):",
        "if self.status is not None and not (101 <= self.status <= 599):",
    ),
    "a text body is accepted": (
        '        if not isinstance(self.body, bytes):\n            raise CaptureError("the body must be bytes")\n',
        "",
    ),
    "a body of any size is accepted": (
        '        if len(self.body) > MAX_BODY_BYTES:\n            raise CaptureError("body too large")\n',
        "",
    ),
    "times are not made UTC": (
        '        object.__setattr__(self, "t_send", ensure_utc(self.t_send))\n        object.__setattr__(self, "received_at", ensure_utc(self.received_at))\n',
        "",
    ),
    "a response may arrive before the request": (
        '        if self.received_at < self.t_send:\n            raise CaptureError("received before it was sent")\n',
        "",
    ),
    "a header name may be upper case": (
        "            if name != name.lower() or not name or not isinstance(value, str):",
        "            if not name or not isinstance(value, str):",
    ),
    "a header may have a non-text value": (
        "            if name != name.lower() or not name or not isinstance(value, str):",
        "            if name != name.lower() or not name:",
    ),
    "a header that looks like a credential is stored": (
        '            if looks_secret(name):\n                raise CaptureError(f"header {name!r} looks like a credential and is never stored")\n',
        "",
    ),
    "headers may be any size": (
        '        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:\n            raise CaptureError("headers too large")\n',
        "",
    ),
    "an empty trace id is accepted": ("0 < len(self.trace_id) <= 128", "len(self.trace_id) <= 128"),
    "a long trace id is accepted": ("0 < len(self.trace_id) <= 128", "0 < len(self.trace_id)"),
    "user information in a URL is stored": (
        '            if parts.username is not None or parts.password is not None:\n                raise CaptureError("the request URL carries user information")\n',
        "",
    ),
    "a user name in a URL is stored": ("if parts.username is not None or parts.password is not None:", "if parts.password is not None:"),
    "a credential in a query is stored": (
        '            if any(looks_secret(k) and v != REDACTED for k, v in parse_qsl(parts.query, keep_blank_values=True)):\n                raise CaptureError("the request URL carries a credential-looking query value")\n',
        "",
    ),
    "a redacted query value is refused": (
        "looks_secret(k) and v != REDACTED for k, v in parse_qsl(parts.query",
        "looks_secret(k) for k, v in parse_qsl(parts.query",
    ),
    # credential words and URL redaction
    "authorization headers are kept": ('"auth", "key", "token"', '"key", "token"'),
    "key headers are kept": ('"auth", "key", "token"', '"auth", "token"'),
    "token headers are kept": ('"key", "token", "secret"', '"key", "secret"'),
    "secret headers are kept": ('"token", "secret", "cookie"', '"token", "cookie"'),
    "cookie headers are kept": ('"secret", "cookie", "sign"', '"secret", "sign"'),
    "signature headers are kept": ('"cookie", "sign", "password"', '"cookie", "password"'),
    "password headers are kept": ('"sign", "password", "passphrase"', '"sign", "passphrase"'),
    "passphrase headers are kept": ('"password", "passphrase", "credential"', '"password", "credential"'),
    "credential headers are kept": ('"passphrase", "credential", "session"', '"passphrase", "session"'),
    "session headers are kept": ('"credential", "session")', '"credential")'),
    "names are matched case-sensitively": (
        "    lowered = name.lower()\n    return any(part in lowered for part in SECRET_NAME_PARTS)",
        "    return any(part in name for part in SECRET_NAME_PARTS)",
    ),
    "redacted headers keep their case": (
        "    return {name.lower(): value for name, value in headers.items() if not looks_secret(name)}",
        "    return {name: value for name, value in headers.items() if not looks_secret(name)}",
    ),
    "redacted headers keep the secret ones": (
        "    return {name.lower(): value for name, value in headers.items() if not looks_secret(name)}",
        "    return {name.lower(): value for name, value in headers.items()}",
    ),
    "URL redaction keeps user information": ('    host = parts.hostname or ""\n', "    host = parts.netloc\n"),
    "URL redaction drops the port": ('    netloc = host + (f":{parts.port}" if parts.port is not None else "")', "    netloc = host"),
    "URL redaction breaks IPv6 hosts": ('    if ":" in host:\n        host = f"[{host}]"\n', ""),
    "URL redaction keeps secret values": ("REDACTED if looks_secret(k) else v", "v"),
    "URL redaction redacts everything": ("REDACTED if looks_secret(k) else v", "REDACTED"),
    "URL redaction drops blank values": ("parse_qsl(parts.query, keep_blank_values=True)])", "parse_qsl(parts.query)])"),
    "URL redaction keeps the fragment": (
        '    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))',
        "    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))",
    ),
    # the store: writing
    "the body is never written": ("        self._write_blob(capture.body_sha256, capture.body)\n", ""),
    "a blob of the wrong size is trusted": (
        "        if path.is_file() and path.stat().st_size == len(body):\n            return\n",
        "        if path.is_file():\n            return\n",
    ),
    "the same capture twice is stored twice": (
        "        known = self._index.get(capture.capture_id)\n        if known is not None:",
        "        known = None\n        if known is not None:",
    ),
    "a different capture under an id is accepted": (
        '                raise CaptureConflict(f"{capture.capture_id} was stored with different content")\n',
        "                pass\n",
    ),
    "re-appending compares the position too": (
        'if {k: v for k, v in known.items() if k not in ("seq", "prev")} != {k: v for k, v in meta.items() if k not in ("seq", "prev")}:',
        "if known != meta:",
    ),
    "a different body under an id is not noticed": (
        'if {k: v for k, v in known.items() if k not in ("seq", "prev")} != {k: v for k, v in meta.items() if k not in ("seq", "prev")}:',
        'if {k: v for k, v in known.items() if k not in ("seq", "prev", "body_sha256", "body_len")} != {k: v for k, v in meta.items() if k not in ("seq", "prev", "body_sha256", "body_len")}:',
    ),
    "the sequence number does not advance": ("        seq = len(self._order)\n", "        seq = 0\n"),
    "the chain does not advance": ("        self._last_hash = _line_hash(line)\n        stored = ", "        stored = "),
    "the line hash is of something else": (
        "        stored = Stored(seq, capture.capture_id, capture.body_sha256, self._last_hash)",
        "        stored = Stored(seq, capture.capture_id, capture.body_sha256, capture.body_sha256)",
    ),
    "a closed store accepts captures": (
        '        if self._lock_fd is None:\n            raise CaptureStoreError("the store is closed")\n',
        "",
    ),
    "a huge metadata line is accepted": (
        '        if len(line) > MAX_LINE_BYTES:\n            raise CaptureError("the capture\'s metadata is too large")\n',
        "",
    ),
    "the writer lock is not taken": (
        "            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n        except OSError as exc:",
        "            pass\n        except OSError as exc:",
    ),
    # the store: reading and recovery
    "bytes after the last newline are not a torn tail": ('        end = data.rfind(b"\\n") + 1', "        end = len(data)"),
    "a torn tail is left in place": (
        '            with open(self._log, "r+b") as handle:\n                handle.truncate(len(complete))',
        '            with open(self._log, "r+b") as handle:\n                handle.truncate(len(data))',
    ),
    "a damaged final line is kept": ("                if final and not tail:", "                if False:"),
    "damage before the end is repaired": ("                if final and not tail:", "                if True:"),
    "a line may be of any length": (
        '        if len(line) > MAX_LINE_BYTES:\n            raise CaptureDamage(f"line {number} is too long")\n',
        "",
    ),
    "a line need not be canonical": (
        '        if canonical.dumps(meta) != line[:-1]:\n            raise CaptureDamage(f"line {number} is not in canonical form")\n',
        "",
    ),
    "a line may have other members": ("        if set(meta) != self.META_KEYS:", "        if not self.META_KEYS <= set(meta):"),
    "a line may lack members": ("        if set(meta) != self.META_KEYS:", "        if not set(meta) <= self.META_KEYS:"),
    "a bad id in a line is accepted": (
        'CAPTURE_ID.fullmatch(capture_id)):\n            raise CaptureDamage(f"line {number} has a bad capture id")',
        'CAPTURE_ID.fullmatch(capture_id)) and False:\n            raise CaptureDamage(f"line {number} has a bad capture id")',
    ),
    "a bad hash in a line is accepted": (
        'SHA256_HEX.fullmatch(sha)):\n            raise CaptureDamage(f"line {number} has a bad body hash")',
        'SHA256_HEX.fullmatch(sha)) and False:\n            raise CaptureDamage(f"line {number} has a bad body hash")',
    ),
    "a bad length in a line is accepted": ("0 <= length <= MAX_BODY_BYTES):", "True):"),
    "a boolean length in a line is accepted": (
        "isinstance(length, int) and not isinstance(length, bool) and 0 <= length",
        "isinstance(length, int) and 0 <= length",
    ),
    "bad headers in a line are accepted": (
        "if not (isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values()) and isinstance(request, dict)):",
        "if False:",
    ),
    "non-text headers in a line are accepted": ("all(isinstance(v, str) for v in headers.values()) and ", ""),
    "a bad status in a line is accepted": (
        "        if not (status is None or (isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599)):",
        "        if False:",
    ),
    "a bad trace id in a line is accepted": (
        '        if not (trace is None or isinstance(trace, str)) or not isinstance(meta["source"], str):',
        "        if False:",
    ),
    "a bad time in a line is accepted": (
        '        try:\n            parse_rfc3339(str(meta["t_send"]))',
        '        try:\n            str(meta["t_send"])',
    ),
    "a bad receive time in a line is accepted": (
        '            parse_rfc3339(str(meta["received_at"]))\n',
        '            str(meta["received_at"])\n',
    ),
    "a wrong sequence number is accepted": ('        if meta["seq"] != number or isinstance(meta["seq"], bool):', "        if False:"),
    "a boolean sequence number is accepted": (
        '        if meta["seq"] != number or isinstance(meta["seq"], bool):',
        '        if meta["seq"] != number:',
    ),
    "the chain is not checked": ('        if meta["prev"] != prev:', "        if False:"),
    "a repeated id is accepted": (
        "        if capture_id in self._index:\n            raise CaptureDamage(",
        "        if False:\n            raise CaptureDamage(",
    ),
    "a missing body is not noticed": (
        "            if not path.is_file():\n                raise CaptureDamage(f\"the body {meta['body_sha256']} of a stored capture is missing\")\n",
        "",
    ),
    "a body of the wrong size is not noticed": ('            if path.stat().st_size != meta["body_len"]:', "            if False:"),
    "orphan bodies are not counted": (
        "        self.recovery.orphan_blobs = sum(1 for _ in self._orphans(referenced))",
        "        self.recovery.orphan_blobs = 0",
    ),
    "orphan detection counts referenced bodies": (
        "if SHA256_HEX.fullmatch(path.name) and path.name not in referenced:",
        "if SHA256_HEX.fullmatch(path.name):",
    ),
    "temporary files are not removed": ("            stale.unlink()\n", "            pass\n"),
    "removed temporaries are not counted": ("            self.recovery.removed_temporaries += 1\n", ""),
    "the capture count is not reported": ("        self.recovery.captures = len(self._order)\n", ""),
    "the repair is not reported": ("            self.recovery.truncated_bytes = len(tail)\n", ""),
    "a body is not re-hashed when read": (
        '        if hashlib.sha256(data).hexdigest() != sha256:\n            raise CaptureDamage(f"the body {sha256} has been altered")\n',
        "",
    ),
    "a missing body is not reported when read": (
        '        except FileNotFoundError as exc:\n            raise CaptureDamage(f"the body {sha256} is missing") from exc',
        '        except FileNotFoundError:\n            return b""',
    ),
    "any text is a body hash": (
        '        if not SHA256_HEX.fullmatch(sha256):\n            raise CaptureStoreError("not a SHA-256 value")\n',
        "",
    ),
    "reading returns a different capture": (
        "            trace_id=trace if isinstance(trace, str) else None,",
        "            trace_id=None,",
    ),
    "reading loses the status": ("            status=status if isinstance(status, int) else None,", "            status=None,"),
    # a store opened to read beside a running writer
    "a read-only store takes the writer's lock": ("        if self._read_only:\n            self._scan()\n            return\n", ""),
    "a read-only store cuts a torn tail": ("        if tail and not self._read_only:", "        if tail:"),
    "a read-only store removes the temporary files of a body being written": (
        "        if not self._read_only:\n            for stale",
        "        if True:\n            for stale",
    ),
    "a read-only store can append": ('        if self._read_only:\n            raise CaptureStoreError("the store is read-only")\n', ""),
    "a read-only store looks for bodies in a folder that is not there": ("        if not blobs.is_dir():\n            return\n", ""),
    # counting a provider's calls in a window
    "the count includes other sources": (
        'if meta["source"] == source and parse_rfc3339',
        "if parse_rfc3339",
    ),
    "the count leaves out a capture sent at the very moment": (">= start)", "> start)"),
    "the count ignores the moment": (
        'if meta["source"] == source and parse_rfc3339(str(meta["t_send"])) >= start)',
        'if meta["source"] == source)',
    ),
    "the count uses the time the answer came": (
        'parse_rfc3339(str(meta["t_send"])) >= start',
        'parse_rfc3339(str(meta["received_at"])) >= start',
    ),
    "the count takes a time without a zone": ("start = ensure_utc(since)", "start = since"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str, tmp_path: Path) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.capture", old, new)
    try:
        found = failures(module, tmp_path)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
