"""Checks planted for the sweep harness's own checks (``mutation_sweep_checks``). Each function is a different set of checks over
``sweep_planted``; what each does to each of its seven changes is stated in ``mutation_sweep_checks``. Not part of the product."""

from __future__ import annotations

import contextlib
import time
import types

_calls = 0  # how many times a check has met the "flaky" change in this process: the message of its error differs every time


def strict(mod: types.ModuleType) -> list[str]:
    """Notices every change that leaves ``double`` wrong."""
    got = mod.double(3)
    return [] if got == 6 else [f"double(3) gave {got!r}, expected 6"]


def lenient(mod: types.ModuleType) -> list[str]:
    """Looks only at ``double(0)``, which is 0 whatever the factor is."""
    got = mod.double(0)
    return [] if got == 0 else [f"double(0) gave {got!r}, expected 0"]


def odd(mod: types.ModuleType) -> list[str]:
    """Meets each wrong ``double(3)`` in a different way: a hang that swallows every ordinary exception, an error from the operating system
    that is not the same twice, and an ordinary error."""
    global _calls
    got = mod.double(3)
    if got == 6:
        return []
    if got == 5:  # n + 2
        while True:
            with contextlib.suppress(Exception):  # the point: only a BaseException gets out of here
                time.sleep(30)
    if got is None:  # return None
        _calls += 1
        raise OSError(f"flaky {_calls}")
    raise ValueError(f"double(3) gave {got!r}")  # n * 3


def steady(mod: types.ModuleType) -> list[str]:
    """Meets one wrong ``double(3)`` with an error from the operating system that is the same every time."""
    got = mod.double(3)
    if got == 9:
        raise OSError("always the same")
    return [] if got == 6 else [f"double(3) gave {got!r}, expected 6"]


def stubborn(mod: types.ModuleType) -> list[str]:
    """Meets a wrong ``double(3)`` with a hang that swallows even a BaseException the first time, so only a limit that comes back gets out."""
    got = mod.double(3)
    if got == 6:
        return []
    with contextlib.suppress(BaseException):  # the point: the first limit is swallowed, the second must still arrive
        time.sleep(30)
    time.sleep(30)
    return ["not reached: the second limit ends the second sleep"]


def twice(mod: types.ModuleType) -> list[str]:
    """For ``sweep_planted_relative``."""
    got = mod.twice(3)
    return [] if got == 6 else [f"twice(3) gave {got!r}, expected 6"]


def broken(mod: types.ModuleType) -> list[str]:
    """Fails on the module as it is."""
    return ["fails on the unchanged module"]
