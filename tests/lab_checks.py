"""Checks of ``hoshitori.lab``, runnable against a mutant: the plain tests of ``tests/test_lab.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_lab


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_lab, "lab", mod)
