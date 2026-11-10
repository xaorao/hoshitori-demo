"""Checks of ``hoshitori.lab_forward``, runnable against a mutant: the plain tests of ``tests/test_lab_forward.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_lab_forward


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_lab_forward, "lab_forward", mod)
