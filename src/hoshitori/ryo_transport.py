"""The network side of the RYO provider: one HTTP exchange per call, and a recorded mode that never touches the network.

``RyoTransport`` implements ``runner.Transport`` on top of ``http_transport.HttpTransport`` (one deadline, a bounded body, screened headers,
verified TLS). What it adds is RYO's own: the routes, and the key. **The key is never in a message.** It is read from a callable at each
exchange, checked to be a plain header value, sent only to the configured host and never written into a capture, an exception or a log.
No key means no request (``AUTH``).

``RecordedTransport`` answers from earlier captures and says so: its provider is ``ryo-recorded``, every capture it makes carries
``"mode": "recorded"``, and the bodies are returned byte for byte as they were first received (a body is evidence and is never edited, so
a recorded answer still says ``data_mode: live`` inside; the provider name is what tells the evidence layer it is a replay).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from hoshitori import canonical
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import TransportFailure
from hoshitori.http_transport import MAX_BODY_BYTES, HttpTransport
from hoshitori.ryo import META_OPERATIONS, PROVIDER, RECORDED_PROVIDER, read_answer
from hoshitori.ryo_catalogue import TOOL_NAME

KEY_SHAPE: Final = re.compile(r"[\x21-\x7e]{8,512}")
NO_AUTH: Final = frozenset({"_health"})


def key_from_env_file(path: Path, name: str = "RYO_MCP_KEY") -> str | None:
    """The value of ``name`` in a ``.env`` file, or None. The value is returned to the caller and never printed or logged here."""
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        if key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


class RyoTransport(HttpTransport):
    def __init__(
        self,
        base_url: str,
        key: Callable[[], str | None],
        clock: Callable[[], datetime],
        *,
        provider: str = PROVIDER,
        allow_insecure_loopback: bool = False,
        max_body_bytes: int = MAX_BODY_BYTES,
        ca_file: str | None = None,
    ) -> None:
        super().__init__(
            base_url,
            clock,
            provider=provider,
            allow_insecure_loopback=allow_insecure_loopback,
            max_body_bytes=max_body_bytes,
            ca_file=ca_file,
        )
        self._key = key

    # --- the request -------------------------------------------------------------------------------------------------------------
    def _route(self, operation: str, args: Mapping[str, Any]) -> tuple[str, str, bytes | None]:
        if operation in META_OPERATIONS:
            if args:
                raise TransportFailure(ErrorClass.INVALID_ARGS, f"{operation} takes no arguments")
            return "GET", self._prefix + META_OPERATIONS[operation], None
        if TOOL_NAME.fullmatch(operation) is None:
            raise TransportFailure(ErrorClass.INVALID_ARGS, "the operation is not a tool name")
        return "POST", f"{self._prefix}/tools/{operation}/call", canonical.dumps(dict(args))

    def _auth_header(self, operation: str) -> dict[str, str]:
        if operation in NO_AUTH:
            return {}
        key = self._key()
        if not key:
            raise TransportFailure(ErrorClass.AUTH, "no builder key is configured")
        if KEY_SHAPE.fullmatch(key) is None:
            raise TransportFailure(ErrorClass.AUTH, "the builder key is not a usable header value")
        return {"Authorization": f"Bearer {key}"}

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        method, path, payload = self._route(operation, args)
        return self._exchange(operation, args, method, path, payload, self._auth_header(operation), timeout_s, capture_id, sent_at)


# --- recorded mode ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Recorded:
    status: int
    headers: Mapping[str, str]
    body: bytes
    trace_id: str | None
    capture_id: str  # the capture this replays


def recording_key(operation: str, args: Mapping[str, Any]) -> tuple[str, str]:
    return operation, canonical.dumps(dict(args)).decode()


class Recordings:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Recorded] = {}

    def add(self, operation: str, args: Mapping[str, Any], recorded: Recorded) -> None:
        self._by_key[recording_key(operation, args)] = recorded

    def lookup(self, operation: str, args: Mapping[str, Any]) -> Recorded | None:
        return self._by_key.get(recording_key(operation, args))

    def __len__(self) -> int:
        return len(self._by_key)

    @classmethod
    def from_store(cls, store: CaptureStore, provider: str = PROVIDER) -> Recordings:
        """The last good capture of each request in a store: HTTP 200 and an answer the readers accept."""
        recordings = cls()
        for capture_id in store.ids():
            capture = store.get(capture_id)
            operation = capture.request.get("operation")
            arguments = capture.request.get("arguments")
            if capture.source != provider or capture.status != 200 or not isinstance(operation, str) or not isinstance(arguments, dict):
                continue
            try:
                read_answer(operation, capture.body)
            except ConnectorError:
                continue
            recordings.add(operation, arguments, Recorded(200, capture.headers, capture.body, capture.trace_id, capture_id))
        return recordings


class RecordedTransport:
    """Answers from ``Recordings``. A request with no recording fails with ``NOT_FOUND``; nothing is invented."""

    def __init__(self, recordings: Recordings, clock: Callable[[], datetime], provider: str = RECORDED_PROVIDER) -> None:
        self._recordings = recordings
        self._clock = clock
        self._provider = provider

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        recorded = self._recordings.lookup(operation, args)
        if recorded is None:
            raise TransportFailure(ErrorClass.NOT_FOUND, "there is no recording of this request")
        request: dict[str, canonical.JsonValue] = {
            "arguments": dict(args),
            "mode": "recorded",
            "operation": operation,
            "recorded_from": recorded.capture_id,
            "surface": "rest",
        }
        received = max(self._clock(), sent_at)
        headers = dict(recorded.headers)
        return RawCapture(
            capture_id,
            self._provider,
            request,
            recorded.status,
            headers,
            recorded.body,
            sent_at,
            received,
            recorded.trace_id,
        )
