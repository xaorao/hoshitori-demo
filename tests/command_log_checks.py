"""Checks of ``hoshitori.command_log``, runnable against a mutant: the plain tests of ``tests/test_command_log.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_command_log


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_command_log, "cl", mod)
