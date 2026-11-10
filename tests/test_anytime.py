"""Anytime-valid comparison: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import anytime
from tests.anytime_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](anytime) == []


def test_real_module_passes_every_check() -> None:
    assert failures(anytime) == []


EQUIVALENT: dict[str, str] = {
    "Mixture.bound|Gt->GtE|self.log_m(middle, v) > threshold|0": "a float landing exactly on the threshold has no weight; both halves close on the same root",
    "Mixture.bound|Gt->GtE|self.log_m(upper, v) > threshold|0": "the same for the bracket's upper end",
    "Mixture.bound|LtE->Lt|upper - lower <= 1e-12 * max(1.0, upper)|0": "a strict tolerance costs at most one more halving; the root is the same",
    "Mixture.bound|Sub->Add|upper - lower|0": "the early stop never fires and the 200 halvings close on the same root",
    "Mixture.bound|max->min|max(1.0, upper)|0": "a stricter tolerance: more halvings, the same root",
    "Mixture.bound|statement dropped|break|1": "the halvings run to their cap and close on the same root",
    "Mixture.bound|int+1|2|0": "tripling instead of doubling still brackets the root",
    "Mixture.bound|statement dropped|raise AnytimeError('no upper limit for the boundary was found')|0": (
        "unreachable: log m grows linearly in s, so 50 doublings from max(v, 1) pass any threshold a float alpha gives"
    ),
    "gamma_p|Lt->LtE|x < a + 1|0": "the same hand-over, at one point",
    "gamma_p|int+1|1|3": "the same hand-over moved by one",
    "gamma_p|int+1|1|0": "the Lentz start 1 / tiny or 2 / tiny: either is effectively infinite and the next term is the same",
    "gamma_p|Lt->LtE|abs(c) < tiny|0": "a guard against division by zero that these arguments never approach",
    "gamma_p|Lt->LtE|abs(d) < tiny|0": "the same guard for d",
    "gamma_p|statement dropped|c = tiny if abs(c) < tiny else c|0": "the guard is never needed for a > 0 and x > a + 1",
    "gamma_p|statement dropped|d = tiny if abs(d) < tiny else d|0": "the same for d",
    "gamma_p|Lt->LtE|abs(step - 1) < EPS|0": "a convergence test at < or <= differs only at an exact float",
    "gamma_p|Lt->LtE|abs(term) < abs(total) * EPS|0": "the same for the series",
    "gamma_p|statement dropped|raise AnytimeError('the continued fraction for Q(a, x) did not converge')|0": (
        "unreachable here: the continued fraction converged within the cap even at a = 1e10 (tried); the series' own failure is tested"
    ),
    "|int+1|10000|0": "an iteration cap one higher changes nothing that converges, and a series that cannot converge still fails",
    "|int+1|200|0": "the bisection stops on its tolerance long before 200 halvings",
    "|int+1|50|0": "one more doubling allowed: the bracket is found long before",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.anytime", "tests.anytime_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
