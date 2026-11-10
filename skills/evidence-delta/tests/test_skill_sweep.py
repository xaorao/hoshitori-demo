"""``evidence_delta.skill`` against its checks: they pass on the real module, and every small change of its code is noticed or listed as
harmless."""

from __future__ import annotations

from evidence_delta import skill
from tests.mutation_sweep import survivors
from tests.skill_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(skill) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|CHANGE_WORDS = {'changed': 'changed', 'unchanged': 'unchanged'}|0": "CHANGE_WORDS is read nowhere in the package or its tests",
    "|str+~|'changed'|0": "CHANGE_WORDS is read nowhere, so changing its key has no effect",
    "|str+~|'unchanged'|0": "CHANGE_WORDS is read nowhere, so changing its key has no effect",
    "|str+~|'changed'|1": "CHANGE_WORDS is read nowhere, so changing its value has no effect",
    "|str+~|'unchanged'|1": "CHANGE_WORDS is read nowhere, so changing its value has no effect",
    "|statement dropped|Key = tuple[str | int, ...]|0": "a type alias used only in annotations, which `from __future__ import annotations` never evaluates",
    "_parse_time|str+~|'Z'|0": "Python 3.12 (the minimum) parses a trailing Z in fromisoformat itself, so the replace is redundant",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("evidence_delta.skill", "tests.skill_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
