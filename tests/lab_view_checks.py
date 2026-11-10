"""Checks of ``hoshitori.lab_view``, runnable against a mutant: the plain tests of ``tests/test_lab_view.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_lab_view


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_lab_view, "lab_view", mod)
