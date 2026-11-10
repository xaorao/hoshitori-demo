"""Checks of ``hoshitori.post_snapshot_job``, runnable against a mutant: the plain tests of ``tests/test_post_snapshot_job.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_post_snapshot_job


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_post_snapshot_job, "post_snapshot_job", mod)
