"""The sweep view: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import sweep_view
from tests.mutation_sweep import survivors
from tests.sweep_view_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(sweep_view) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "read|Lt->LtE|end < 0|0": "the closing fence is searched after the opening one, so it can never be found at position zero: below zero and at most zero are the same test",
    "read|int+1|0|1": "the same test with the bound moved from zero to one: a closing fence found after the opening one is at position eight or more",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.sweep_view", "tests.sweep_view_checks")
    assert total > 80, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
