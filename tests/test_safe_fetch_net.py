"""The network side of the safe fetch client: every check passes on the real module, and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import safe_fetch_net
from tests.mutation_sweep import survivors
from tests.safe_fetch_net_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(safe_fetch_net) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.safe_fetch_net", "tests.safe_fetch_net_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"
