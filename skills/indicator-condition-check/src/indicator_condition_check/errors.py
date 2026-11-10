"""Error types. A validation error is raised before any network call is made."""
from __future__ import annotations


class InputError(ValueError):
    """The request is malformed. `field` is a JSON-pointer-like path, `example` a working call."""

    code = "INVALID_ARGUMENTS"

    def __init__(self, field: str, message: str, example: dict | None = None):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.detail = message
        self.example = example

    def to_envelope(self, trace_id: str) -> dict:
        details: dict = {"field": self.field}
        if self.example is not None:
            details["example"] = self.example
        return {"code": self.code, "message": f"{self.field}: {self.detail}", "details": details, "trace_id": trace_id}


# Fixed, trusted wording per failure kind. Upstream response text is NEVER copied into an error:
# it is untrusted input and could carry instructions aimed at a model reading the envelope.
KIND_MESSAGES = {
    "rate_limited": "the venue is rate limiting requests",
    "timeout": "the venue did not answer within the time budget",
    "unavailable": "the venue is unavailable or returned a server error",
    "geo_blocked": "the venue refused the request (region or access policy)",
    "not_listed": "the symbol is not listed on this venue",
    "unsupported_interval": "the venue has no candles of this size",
    "bad_response": "the venue returned a reply that could not be used",
    "cooling_down": "the venue is in a cool-down after rate limiting",
}
RETRYABLE_KINDS = frozenset({"rate_limited", "timeout", "unavailable"})


class VenueError(Exception):
    """A single venue could not supply candles. `kind` is stable and machine readable."""

    KINDS = tuple(KIND_MESSAGES)

    def __init__(
        self,
        venue: str,
        kind: str,
        message: str | None = None,
        http_status: int | None = None,
        retry_after_s: float | None = None,
        venue_code: str | None = None,
    ):
        assert kind in self.KINDS, kind
        self.message = KIND_MESSAGES[kind]
        super().__init__(f"{venue}: {kind}: {self.message}")
        self.venue = venue
        self.kind = kind
        self.http_status = http_status
        self.retry_after_s = retry_after_s
        self.venue_code = venue_code

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS
