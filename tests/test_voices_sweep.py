"""Every small change of ``hoshitori.voices`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "_local|int+1|1|0": "a tag has at most one closing brace, so splitting on the first or on the first two gives the same last part",
    "at|str+~|'Z'|0": "Python 3.12 reads a time ending in Z itself, so leaving the Z in place changes nothing",
    "convergence|int+1|0|1": "the time is replaced by a number only for a post with no time, and every such post gets the same number",
    "convergence|statement dropped|reason = ''|0": "the reason is set again on every path that reads it",
    "parse_feed_xml|statement dropped|continue|0": "an element that is not an item has no title and no id, so the check below it skips it anyway",
    "parse_feed|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
    "parse_inbox|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
    "parse_telegram_page|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
    "parse_x_search|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
    "similarity|Or->And|not a or not b|0": "with one set empty the overlap is zero, and the jaccard that follows is zero too, the same answer",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.voices", "tests.voices_checks")
    assert total > 400, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
