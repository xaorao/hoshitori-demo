"""The belief-revision benchmark: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import belief_revision
from tests.belief_revision_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](belief_revision) == []


def test_real_module_passes_every_check() -> None:
    assert failures(belief_revision) == []


SHIFT = "every rank is moved by the same constant, and a correlation does not change when all the ranks of both lists are shifted"
CENTRED = "the ranks are centred first, so the terms sum to zero and the product of a centred and an uncentred factor has the same sum"
EQUIVALENT: dict[str, str] = {
    "_ranks|Add->Sub|(start + end) / 2 + 1|0": SHIFT,
    "_ranks|int+1|1|1": SHIFT,
    "spearman|Sub->Add|a - mx|1": CENTRED,
    "spearman|Sub->Add|b - my|1": CENTRED,
    "spearman|True<->False|True|0": "the lists are checked to have the same length before the zip, so `strict` can never raise",
    "evaluate|Gt->GtE|move > 0|0": "this is inside the branch where the size of the move is at least 0.06, so the move is never zero",
    "evaluate|Gt->GtE|case.direction > 0|0": "it is read only when a revision is the right response, and then the direction is plus or minus one (pinned by the cases check)",
    "evaluate|any->all|any((case.revise for case in cases))|0": "every case of a class agrees on whether a revision is right (pinned by the cases check), so any and all give the same answer",
    "report|any->all|any((case.revise for case in CASES if case.update_class is r.update_class))|0": "the same: the cases of a class agree",
    "evaluate|int+1|0|1": "the reference action only sets the shift that is reported; the label did not depend on it in 400 random paired tables of three actions",
    "evaluate|int+1|0|4": "the same reference action",
    "evaluate|max->min|max({t[0] for t in triples}, key=[t[0] for t in triples].count)|0": "the same reference action",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.belief_revision", "tests.belief_revision_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
