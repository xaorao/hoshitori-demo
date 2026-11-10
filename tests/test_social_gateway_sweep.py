"""Every small change of ``hoshitori.social_gateway`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "BlueskyFeedAdapter.normalise|str+~|'@'|0": "the actor was already checked to be a handle or an identifier, neither of which can start with a tilde",
    "HyperliquidWalletAdapter.request_body|True<->False|True|0": "the two keys of the body are already in sorted order, so sorting changes nothing",
    "XSearchAdapter._parse|return None|return voices.parse_x_search(body, str(args['handle']))|0": "the classification reads only whether the answer parses; the parsed value is not used",
    "_Adapter._parse|statement dropped|raise NotImplementedError|0": "the base class is never used on its own; both adapters define their own reading",
    "_Adapter.classify|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.social_gateway", "tests.social_gateway_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
