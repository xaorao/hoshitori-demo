"""A tiny recorder for the mutation checks: each comparison is named, and a failed one is listed instead of raised."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any


def same(got: Any, want: Any) -> bool:
    """Equal with the same types all the way down (True is not 1), and dict order ignored."""
    if type(got) is not type(want):
        return False
    if isinstance(got, dict):
        return got.keys() == want.keys() and all(same(got[k], want[k]) for k in got)
    if isinstance(got, (list, tuple)):
        return len(got) == len(want) and all(same(g, w) for g, w in zip(got, want, strict=True))
    return bool(got == want)


class Case:
    """Collects failures. ``eq`` compares values exactly (types included, so True is not 1)."""

    def __init__(self, mod: ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: Any, want: Any) -> None:
        if not same(got, want):
            self.bad.append(f"{label}: got {got!r}, wanted {want!r}")

    def true(self, label: str, value: bool) -> None:
        if value is not True:
            self.bad.append(f"{label}: not true")

    def run(self, label: str, call: Callable[[], Any]) -> Any:
        """The call's value; an exception is listed and None returned."""
        try:
            return call()
        except Exception as exc:
            self.bad.append(f"{label} raised {type(exc).__name__}: {exc}")
            return None

    def raises(self, label: str, call: Callable[[], object], field: Any, detail: str = "", example: Any = None) -> None:
        """The call must raise the given exception class, or InputError with this field, this detail and this example."""
        if isinstance(field, type):
            try:
                call()
            except field:
                return
            except Exception as exc:
                self.bad.append(f"{label}: raised {type(exc).__name__}, wanted {field.__name__}")
                return
            self.bad.append(f"{label}: did not raise")
            return
        try:
            call()
        except Exception as exc:
            got = (type(exc).__name__, getattr(exc, "field", None), getattr(exc, "detail", None), getattr(exc, "example", None))
            if got != ("InputError", field, detail, example):
                self.bad.append(f"{label}: raised {got!r}, wanted {('InputError', field, detail, example)!r}")
            return
        self.bad.append(f"{label}: did not raise")
