"""Settlement of the two price contracts: every check and golden case passes on the real module, and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import settlement
from tests.mutation_sweep import survivors
from tests.settlement_checks import failures


def test_real_module_passes_every_check_and_every_golden_case() -> None:
    assert failures(settlement) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|int+1|60|0": "the decimal precision (60 digits): any larger value is as exact for every price a venue can print",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.settlement", "tests.settlement_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
