"""The file of a model arm's challenge: the checks pass on the real module and every small change of its code is noticed or listed as
harmless."""

from __future__ import annotations

from hoshitori import model_challenge_file
from tests.model_challenge_file_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(model_challenge_file) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
# The sweep has not been run on this module yet (agent C2 was told not to run sweeps): the list is empty until it is.
EQUIVALENT: dict[str, str] = {
    "bind|int+1|1|0": "the neutral item's id does not depend on how many items its size is taken from, so one or two give the same item",
    "_decision|int+1|0|0": "a member of the stand-in decision that neither action_class nor proposal_class reads",
    "_tally|IsNot->Is|x.gate is not Gate.NONE|0": "negating every run's gate leaves each pair's comparison with its full run the same",
    "|statement dropped|Derived = tuple[str, str | None, int, paired_stats.Pairs | None, paired_stats.Pairs | None, "
    "paired_stats.Verdict | None]|0": "a type alias that only the type checker reads",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.model_challenge_file", "tests.model_challenge_file_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
