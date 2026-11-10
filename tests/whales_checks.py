"""Checks of ``hoshitori.whales``, runnable against a mutant: the plain tests of ``tests/test_whales.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_whales


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_whales, "w", mod)
