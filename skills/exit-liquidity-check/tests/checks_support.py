"""A tiny recorder for the sweep checks: each check compares a value with the exact expected one and notes the difference."""

from __future__ import annotations

import types
from typing import Any


class Case:
    """Collects failures instead of raising, so a check function can return the list a sweep reads."""

    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.failed: list[str] = []

    @property
    def bad(self) -> list[str]:
        return self.failed

    def true(self, label: str, value: bool) -> None:
        if value is not True:
            self.failed.append(f"{label}: got {value!r}, want True")

    def run(self, label: str, fn: Any) -> Any:
        """Call ``fn``; an exception is a failure, not a crash of the whole check."""
        try:
            return fn()
        except Exception as err:
            self.failed.append(f"{label}: raised {type(err).__name__}: {err}")
            return None

    def eq(self, label: str, got: Any, want: Any) -> None:
        if got != want or type(got) is not type(want):
            self.failed.append(f"{label}: got {got!r}, want {want!r}")

    def near(self, label: str, got: float | None, want: float, tol: float = 1e-9) -> None:
        if got is None or abs(got - want) > tol * max(1.0, abs(want)):
            self.failed.append(f"{label}: got {got!r}, want {want!r}")

    def raises(self, label: str, fn: Any, exc: type[BaseException], text: str | None = None) -> None:
        try:
            fn()
        except exc as err:
            if text is not None and text not in str(err):
                self.failed.append(f"{label}: message {str(err)!r} lacks {text!r}")
        except BaseException as err:
            self.failed.append(f"{label}: raised {type(err).__name__}, want {exc.__name__}")
        else:
            self.failed.append(f"{label}: did not raise")
