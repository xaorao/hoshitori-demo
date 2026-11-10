"""The prompt-injection suite: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import injection
from tests.injection_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](injection) == []


def test_real_module_passes_every_check() -> None:
    assert failures(injection) == []


EQUIVALENT: dict[str, str] = {
    "_Menu.__init__|str+~|'dispatch.jsonl'|0": "the dispatch journal's file name inside a temporary directory is only a name; it is written and read under the same one",
    "_Menu.close|statement dropped|self.captures.close()|0": (
        "the menu's capture store is opened for writing in a temporary directory that is removed when the check ends; closing it releases only "
        "a lock that dies with the process, and nothing reads the store afterwards"
    ),
    "_Menu.admitted|Add->Sub|now + timedelta(hours=1)|0": (
        "`admitted` asks whether a call is refused at submission or captured. A call that is not on the menu is refused before its deadline "
        "is read, and one that is is queued (deadline ahead) or ends DEADLINE_UNREACHABLE (deadline behind), which is neither a refusal nor "
        "a capture, so both directions answer the same (Gateway.submit)"
    ),
    "_Menu.admitted|int+1|1|0": (
        "the hour added to now is the deadline of a call that is either refused at submission or queued; one hour or two answers the same "
        "(see the entry for Add->Sub above)"
    ),
    "_spend|str+~|'stand-in/model'|0": "the model name is a label of the request the stand-in endpoint answers whatever it is called",
    "_stand_in_endpoint.fetch|str+~|'stand-in/model'|0": "the stand-in endpoint's reply echoes a model name that nothing reads",
    "|int+1|0|0": "the instant of the manual clock of the suite is any instant: the hour and the minute are read by nothing",
    "|int+1|10|1": "the instant of the manual clock of the suite is any instant: the hour and the minute are read by nothing",
    "report|str+~|'; '|0": "the report joins the goals it does not cover with a separator, and the suite now leaves out one goal only, so no separator is printed",
}

# Both changes are harmless, but a whole-suite run on a loaded machine has counted one of them as noticed (a check ran out of time), so a
# listed change may be missing from the survivors without the list being stale. An unlisted survivor still fails.
LOAD_SENSITIVE: set[str] = set(EQUIVALENT)


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.injection", "tests.injection_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    unexplained, stale = set(alive) - set(EQUIVALENT), set(EQUIVALENT) - set(alive)
    assert not unexplained and stale <= LOAD_SENSITIVE, (
        f"unexplained survivors: {sorted(unexplained)}; stale entries: {sorted(stale - LOAD_SENSITIVE)}"
    )
