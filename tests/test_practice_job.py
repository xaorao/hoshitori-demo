"""Practice pass: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import practice_job
from tests.mutation_sweep import survivors
from tests.practice_job_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(practice_job) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "practice_pass|sorted removed|sorted(((venue, raw, symbol) for (venue, raw), symbol in symbol_of.items()))|0": "the order of the tokens in the key only decides whether a pass finds the day's clusters already read; the clusters are the same either way",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.practice_job", "tests.practice_job_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
