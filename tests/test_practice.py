"""Practice decisions: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import practice
from tests.mutation_sweep import survivors
from tests.practice_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(practice) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_plan|int+1|1|0": "the amount of the probe purchase only asks whether the bar exists; any positive amount asks the same",
    "_sized|int+1|1|0": "a book that holds only long positions has a gross that is its net, so the gross limit is the net limit in effect, and the room it is given to the kernel is the same either way",
    "_sized|int+1|2|0": "the net limit given to the kernel is looser or tighter by a room that the gross limit already enforces, because a book of long positions has equal gross and net",
    "|int+1|60|0": "sixty-one digits of precision instead of sixty changes no figure the book holds (eight places of a price of at most twenty digits)",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.practice", "tests.practice_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
