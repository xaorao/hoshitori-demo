"""The network side of an exchange adapter: a public, keyless HTTP exchange. It sends nothing the adapter did not ask for and no credential
at all; everything else (the deadline, the bounded body, the screened headers, verified TLS) is ``HttpTransport``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import TransportFailure
from hoshitori.http_transport import HttpTransport
from hoshitori.venue_base import VenueAdapter


class VenueTransport(HttpTransport):
    def __init__(
        self,
        base_url: str,
        adapter: VenueAdapter,
        clock: Callable[[], datetime],
        *,
        allow_insecure_loopback: bool = False,
        max_body_bytes: int | None = None,
        ca_file: str | None = None,
    ) -> None:
        super().__init__(
            base_url,
            clock,
            provider=adapter.provider,
            allow_insecure_loopback=allow_insecure_loopback,
            max_body_bytes=adapter.max_answer_bytes if max_body_bytes is None else max_body_bytes,
            ca_file=ca_file,
        )
        self._adapter = adapter

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        try:
            request = self._adapter.build_request(operation, args)
        except (ConnectorError, KeyError, ValueError, TypeError) as exc:  # VenueDataError is a ValueError
            raise TransportFailure(ErrorClass.INVALID_ARGS, f"the request could not be built: {type(exc).__name__}") from None
        return self._exchange(
            operation, args, request.method, self._prefix + request.path, request.body, {}, timeout_s, capture_id, sent_at
        )
