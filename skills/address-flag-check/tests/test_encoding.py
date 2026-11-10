"""Encoding: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import encoding
from tests.encoding_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(encoding) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_permute|int+1|0|1": "b = [1] * 25: the pi loop writes all 25 cells of b (x, y -> y, 2x + 3y mod 5 is a permutation) before any is read",
    "_permute|int+1|25|0": "b = [0] * 26: the loops index b only at 0..24, so the 26th cell is never read",
    "_permute|int+1|5|5": "d's comprehension over range(6): the extra d[5] is never read, since d is indexed as d[i % 5]",
    "_polymod|int+1|5|0": "range(6): chk stays below 2**30 (25 bits shifted by 5, xor values and generators below 2**30), so bit 5 of top is 0",
    "_rotations|int+1|25|0": "offsets = [0] * 26: _ROT gains an unread 26th entry; _ROT is indexed only at x + 5 * y, at most 24",
    "_round_constants.rc|int+1|1|1": "rc(0) returns 2 instead of 1: rc's result is used only as a truth value (if rc(...))",
    "_round_constants.rc|int+1|255|0": "t % 256 == 0: t = j + 7 * i is at most 6 + 7 * 23 = 167, so t % 255 and t % 256 are both t",
    "_round_constants.rc|int+1|255|1": "range(t % 256): t is at most 167, so t % 255 and t % 256 are both t",
    "_round_constants.rc|int+1|256|0": "r & 0x101: bit 0 of r is 0 right after r <<= 1, where the test runs, so only bit 8 can make it true",
    "b58decode|str+~|'1'|0": "lstrip('1~'): by then every character is in the base58 alphabet, which has no '~', so only '1's are stripped",
    "convertbits|Sub->Add|frombits + tobits - 1|0": "a wider max_acc keeps only bits that every read masks off: reads use bits below frombits + tobits - 1",
    "convertbits|int+1|0|0": "acc = 1: the stray bit sits at or above the bits still unread, every read masks it with maxv, max_acc drops it",
    "convertbits|int+1|1|3": "(2 << n) - 1 is a wider max_acc, the same as the Sub->Add entry: reads use bits below frombits + tobits - 1",
    "eip55|int+1|16|0": "int(digit, 17): every character of a hex digest is 0-9 or a-f, which base 17 reads as the same value",
    "eip55|str+~|'ascii'|0": "Python's codec lookup normalises the name 'ascii~' to 'ascii' (codecs.lookup('ascii~').name == 'ascii')",
    "sponge256|int+1|25|0": "lanes = [0] * 26: absorption touches lanes 0..16, the permutation 0..24 and the output 0..3; lane 25 is never read",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.encoding", "tests.encoding_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
