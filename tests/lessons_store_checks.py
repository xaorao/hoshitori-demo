"""Checks of ``hoshitori.lessons_store``, runnable against a mutant: the plain tests of ``tests/test_lessons_store.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_lessons_store


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_lessons_store, "lessons_store", mod)
