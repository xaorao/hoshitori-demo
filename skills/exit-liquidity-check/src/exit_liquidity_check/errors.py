"""Error types and the fixed wording of every failure. Upstream text is never copied into an error or an answer."""

from __future__ import annotations

from typing import Any


class InputError(ValueError):
    """The request is malformed. Raised before any network call. ``field`` names the argument, ``example`` is a working call."""

    code = "INVALID_ARGUMENTS"

    def __init__(self, field: str, message: str, example: dict[str, Any] | None = None) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.detail = message
        self.example = example

    def to_envelope(self, trace_id: str) -> dict[str, Any]:
        details: dict[str, Any] = {"field": self.field}
        if self.example is not None:
            details["example"] = self.example
        return {"code": self.code, "message": f"{self.field}: {self.detail}", "details": details, "trace_id": trace_id}


# The closed set of reasons a source can be unavailable, each with its fixed text (house wording, as the indicator skill's KIND_MESSAGES).
# tls_error is separate: a certificate this machine's trust store cannot verify is not "could not be reached".
REASON_TEXT: dict[str, str] = {
    "timeout": "the source did not answer within the time budget",
    "rate_limited": "the source is rate limiting requests",
    "server_error": "the source returned a server error",
    "network_error": "the source could not be reached",
    "bad_response": "the source returned a reply that could not be used",
    "cooling_down": "the source is in a cool-down after rate limiting",
    "budget_exhausted": "the time budget for this call ran out",
    "tls_error": "this machine could not check the source's TLS certificate",
}


class SourceError(Exception):
    """One source could not be read. ``reason`` is one of REASON_TEXT; the message is that fixed text, never the upstream's."""

    def __init__(self, reason: str, http_status: int | None = None, retry_after_s: float | None = None) -> None:
        if reason not in REASON_TEXT:
            raise ValueError(f"unknown reason {reason!r}")
        super().__init__(REASON_TEXT[reason])
        self.reason = reason
        self.http_status = http_status
        self.retry_after_s = retry_after_s
