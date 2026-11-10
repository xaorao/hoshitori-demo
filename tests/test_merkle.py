"""Merkle tree and proofs against the Certificate Transparency reference, the Go library and brute force."""

from __future__ import annotations

import hashlib
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import merkle as M
from tests.merkle_checks import failures, leaf_data
from tests.mutation import mutant


def test_reference_checks_pass() -> None:
    assert failures(M) == []


def naive_root(leaves: list[bytes]) -> bytes:
    """RFC 9162 MTH by direct recursion on a list; shares no code with the tree class."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = 1
    while k * 2 < n:
        k *= 2
    return hashlib.sha256(b"\x01" + naive_root(leaves[:k]) + naive_root(leaves[k:])).digest()


def build(n: int) -> tuple[M.MerkleTree, list[bytes]]:
    leaves = [M.leaf_hash(leaf_data(i)) for i in range(n)]
    return M.MerkleTree(leaves), leaves


def test_every_root_matches_the_naive_recursion() -> None:
    tree, leaves = build(130)
    for size in range(131):
        assert tree.root(size) == naive_root(leaves[:size])


def test_every_proof_of_every_tree_up_to_70_verifies() -> None:
    tree, _ = build(70)
    for size in range(1, 71):
        root = tree.root(size)
        for index in range(size):
            assert M.verify_inclusion(tree.leaf_hash_at(index), index, size, tree.inclusion_proof(index, size), root)
        for first in range(1, size + 1):
            assert M.verify_consistency(first, size, tree.consistency_proof(first, size), tree.root(first), root)


def test_incremental_append_does_not_change_earlier_roots() -> None:
    tree = M.MerkleTree()
    seen = []
    for i in range(60):
        tree.append(leaf_data(i))
        seen.append(tree.root())
        # asking for an earlier size after growth returns the root recorded at that size
        assert [tree.root(k) for k in range(1, i + 2)] == seen


def test_a_proof_does_not_authenticate_its_size() -> None:
    # Documented property: with the same path shape a proof for size n also verifies for another size when the
    # root is recomputed for that size. The size must therefore come from a verified checkpoint.
    tree, _ = build(9)
    path = tree.inclusion_proof(0, 9)
    assert M.verify_inclusion(tree.leaf_hash_at(0), 0, 9, path, tree.root(9))
    assert not M.verify_inclusion(tree.leaf_hash_at(0), 0, 9, path, tree.root(8))


@pytest.mark.parametrize(
    "args",
    [
        (b"", 0, 1, [], b"\x00" * 32),  # empty leaf hash
        (b"\x00" * 32, 0, 1, [], b""),  # empty root
        (b"\x00" * 32, -1, 1, [], b"\x00" * 32),  # negative index
        (b"\x00" * 32, 1, 1, [], b"\x00" * 32),  # index at size
        (b"\x00" * 32, 0, 0, [], b"\x00" * 32),  # empty tree
        (b"\x00" * 32, 0, 2, [b"\x00" * 31], b"\x00" * 32),  # short path element
        (b"\x00" * 32, 0, 2, [b"\x00" * 33], b"\x00" * 32),  # long path element
        (b"\x00" * 32, 0, 2, ["not bytes"], b"\x00" * 32),  # wrong type in path
        ("x", 0, 1, [], b"\x00" * 32),  # wrong type leaf
        (b"\x00" * 32, True, 1, [], b"\x00" * 32),  # bool is not an index
        (b"\x00" * 32, 0, 1, None, b"\x00" * 32),  # missing path
        (bytearray(32), 0, 1, [], b"\x00" * 32),  # bytearray is not bytes
    ],
)
def test_verify_inclusion_never_raises_on_hostile_input(args: tuple) -> None:  # type: ignore[type-arg]
    assert M.verify_inclusion(*args) is False


def test_verify_consistency_never_raises_and_refuses_size_zero() -> None:
    root = b"\x00" * 32
    assert M.verify_consistency(0, 1, [], root, root) is False
    assert M.verify_consistency(2, 1, [], root, root) is False
    assert M.verify_consistency(1, 2, [b"x"], root, root) is False
    assert M.verify_consistency(1, 2, None, root, root) is False  # type: ignore[arg-type]
    assert M.verify_consistency(1.0, 2, [], root, root) is False  # type: ignore[arg-type]
    assert M.verify_consistency(1, 1, [], root, root) is True
    assert M.verify_consistency(1, 1, [], root, b"\x01" * 32) is False


def test_tree_rejects_bad_arguments() -> None:
    tree, _ = build(3)
    for call in (
        lambda: tree.inclusion_proof(3),
        lambda: tree.inclusion_proof(-1),
        lambda: tree.root(4),
        lambda: tree.consistency_proof(0),
        lambda: tree.consistency_proof(4),
        lambda: tree.leaf_hash_at(9),
        lambda: tree.append_leaf_hash(b"short"),
        lambda: M.node_hash(b"x", b"y" * 32),
    ):
        with pytest.raises(M.MerkleError):
            call()


@settings(max_examples=150, deadline=None)
@given(st.integers(1, 200), st.randoms(use_true_random=False))
def test_random_trees_proofs_verify_and_single_mutations_fail(n: int, rng: random.Random) -> None:
    tree, leaves = build(n)
    index = rng.randrange(n)
    root = tree.root()
    path = tree.inclusion_proof(index)
    assert M.verify_inclusion(leaves[index], index, n, path, root)
    if path:
        i = rng.randrange(len(path))
        flipped = bytearray(path[i])
        flipped[rng.randrange(32)] ^= 1 << rng.randrange(8)
        assert not M.verify_inclusion(leaves[index], index, n, [*path[:i], bytes(flipped), *path[i + 1 :]], root)
    first = rng.randint(1, n)
    proof = tree.consistency_proof(first)
    assert M.verify_consistency(first, n, proof, tree.root(first), root)
    if proof:
        i = rng.randrange(len(proof))
        flipped = bytearray(proof[i])
        flipped[rng.randrange(32)] ^= 1 << rng.randrange(8)
        assert not M.verify_consistency(first, n, [*proof[:i], bytes(flipped), *proof[i + 1 :]], tree.root(first), root)


MUTATIONS = {
    "leaf prefix equals node prefix": ('return sha256(b"\\x00" + data)', 'return sha256(b"\\x01" + data)'),
    "node prefix equals leaf prefix": ('return sha256(b"\\x01" + _require_hash(left', 'return sha256(b"\\x00" + _require_hash(left'),
    "split at the largest power of two not above n": ("return 1 << ((n - 1).bit_length() - 1)", "return 1 << (n.bit_length() - 1)"),
    "hash length not enforced": ("return type(value) is bytes and len(value) == HASH_LENGTH", "return type(value) is bytes"),
    "inclusion end check dropped": ("return sn == 0 and computed == root", "return computed == root"),
    "inclusion index bound dropped": (
        "or not 0 <= index < size:\n        return False\n    fn, sn, computed",
        "or not 0 <= index:\n        return False\n    fn, sn, computed",
    ),
    "consistency end check dropped": (
        "return first_computed == first_root and second_computed == second_root and sn == 0",
        "return first_computed == first_root and second_computed == second_root",
    ),
    "consistency power-of-two prepend dropped": ("if first & (first - 1) == 0:\n        nodes.insert(0, first_root)", "pass"),
    "consistency first root not checked": (
        "return first_computed == first_root and second_computed == second_root and sn == 0",
        "return second_computed == second_root and sn == 0",
    ),
    "equal sizes accept a path": ("return len(path) == 0 and first_root == second_root", "return first_root == second_root"),
    "perfect subtree cache keyed on size only": ("self._perfect[(start, count)] = value", "self._perfect[(0, count)] = value"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    try:
        found = failures(mutant("hoshitori.merkle", old, new))
    except Exception:  # a mutant that crashes (for example by recursing forever) is detected
        return
    assert found, f"mutant not detected: {name}"
