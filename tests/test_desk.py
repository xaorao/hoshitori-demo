"""The Desk's run: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import desk
from tests.desk_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk) == []


EQUIVALENT: dict[str, str] = {
    "_Run.affordable|int+1|0|2": "the seconds are whole numbers, so a check whose worst time is not known is affordable exactly when at least one second is left, as with a worst time of one",
    "_Run.state|sorted removed|sorted(self.budget.calls.items())|0": "a record is written in canonical form, which orders the keys of every object, so the order the dictionary was built in is not seen",
    "_Run.state|sorted removed|sorted(self.calls.items())|0": "a record is written in canonical form, which orders the keys of every object, so the order the dictionary was built in is not seen",
    "_go|sorted removed|sorted(set(evidence_ids))|1": "the grounding port only asks whether a cited id is among them, so the order of the ids it is given is not seen",
    "_go|str+~|', '|0": "the hypothesis has one part a check can settle, so a list of missing parts never has two to separate",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk", "tests.desk_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
