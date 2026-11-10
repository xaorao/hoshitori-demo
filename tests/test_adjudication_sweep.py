"""Every small change of ``hoshitori.adjudication`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "Adjudicator._groups|statement dropped|find(who)|0": "the network step registers the identity in the same way a line later",
    "Case|str+~|'SUBMITTED'|0": "the default state is replaced by the first step of every submission before the case is seen",
    "Adjudicator._groups.find|statement dropped|parent[x] = parent[parent[x]]|0": "path compression only makes the search faster; the groups are the same",
    "Adjudicator._groups|Add->Sub|i + 1|0": "the earlier pairs are compared again; a pair is united once however many times it is seen",
    "Adjudicator._groups|sorted removed|sorted(self._submissions)|0": "submissions are recorded in time order by a clock that does not run back",
    "Adjudicator._groups|int+1|1|2": "the burst counts identities; the same identity under several networks is united with itself, which changes nothing",
    "Adjudicator.appeal|Or->And|case.state != 'DECIDED' or case.decided_at is None|0": "an item that was never decided has no decision time, so both conditions are true together",
    "Adjudicator.appeal|int+1|1|0": "the count of appeals is only tested for being zero",
    "Adjudicator.submit|str+~|'DUPLICATE'|1": "the first submission of a claim is never a duplicate, so it is found first either way",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.adjudication", "tests.adjudication_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
