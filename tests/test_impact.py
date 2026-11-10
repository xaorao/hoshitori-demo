"""The Today impact ranking: every check passes on the real module and every small change of its code is noticed or listed."""

from __future__ import annotations

from hoshitori import impact
from tests.impact_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(impact) == []


EQUIVALENT: dict[str, str] = {
    "slate|int+1|0|2": (
        "the default count for a token not yet seen in the overlap test: 1 is still below the limit of 2, so an unseen token is never limited"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.impact", "tests.impact_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
