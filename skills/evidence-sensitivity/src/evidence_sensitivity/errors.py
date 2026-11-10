"""The one error type: a request that is malformed. It names its field and carries a working example, and never repeats the input."""

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
