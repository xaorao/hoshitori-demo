"""Checks of ``hoshitori.arena``, runnable against a mutant: the plain tests of ``tests/test_arena.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_arena


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_arena, "arena", mod)
