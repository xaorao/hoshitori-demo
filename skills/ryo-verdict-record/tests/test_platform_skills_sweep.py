"""The platform shape against every small change of ``platform_skills.py``: the checks pass on the real module and each change of its code is noticed or
listed as harmless."""

from __future__ import annotations

import ryo_verdict_record.platform_skills as platform_skills
from tests.mutation_sweep import survivors
from tests.platform_skills_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(platform_skills) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("ryo_verdict_record.platform_skills", "tests.platform_skills_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
