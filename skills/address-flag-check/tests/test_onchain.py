"""On-chain reads: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import onchain
from tests.mutation_sweep import survivors
from tests.onchain_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(onchain) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_json|statement dropped|raise _bad() from None|0": (
        "_json then returns None, and each caller refuses None with the same bad_response (decode_tron_flag, parse_mempool: not a dict; "
        "parse_tron_block: the TypeError it catches)"
    ),
    "decode_tron_flag|int+1|16|0": (
        "a 64-hex-digit word is 0 or 1 in base 17 exactly when it is in base 16 (every digit but the last is 0), with the same value"
    ),
    "parse_height|str+~|'ascii'|0": "Python's codec lookup normalises the name 'ascii~' to 'ascii' (codecs.lookup('ascii~').name == 'ascii')",
    "read_evm|statement dropped|last = SourceError('network_error')|0": (
        "the loop runs at least once (nodes or EVM_NODES[chain] is never empty), and found stays None only if every pass set last"
    ),
    "read_evm|statement dropped|url = (nodes or EVM_NODES[chain])[-1]|0": (
        "found is None only after the loop ran over every node without a break, so the loop variable url is already the last node"
    ),
    "read_tron|statement dropped|last = SourceError('network_error')|0": (
        "every call (skill.run passes the two TRON_NODES) runs the loop, and it leaves without returning only after a pass set last; "
        "an empty list fails before returning either way (IndexError at nodes[-1] here)"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.onchain", "tests.onchain_checks")
    assert total > 350, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
