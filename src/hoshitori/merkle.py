"""Merkle tree, inclusion and consistency proofs: RFC 9162 section 2.1 with SHA-256.

* leaf hash ``SHA-256(0x00 || data)``, node hash ``SHA-256(0x01 || left || right)``, empty tree ``SHA-256("")``;
  the split of an n-leaf tree is the largest power of two strictly below n.
* Every hash on the wire is exactly 32 bytes. The bare RFC arithmetic would accept two equal empty strings as
  hashes, so the verifier functions reject any other length (the Go reference library does the same).
* Proof *generation* uses the recursive definitions (PATH, SUBPROOF); proof *verification* uses the iterative
  algorithms of sections 2.1.3.2 and 2.1.4.2. They are different code paths on purpose, so each checks the other.
* The tree size and root used to check a proof must come from a checkpoint whose signature has already verified.
  A proof does not authenticate its own size: for some path shapes a proof for size n also verifies for n +/- 1, so callers never
  take the size from the proof.

The verifier functions never raise on hostile input: a malformed argument is a failed verification.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Final

HASH_LENGTH: Final = 32


class MerkleError(ValueError):
    """A tree operation was given an invalid argument."""


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


EMPTY_ROOT: Final = sha256(b"")


def _is_hash(value: object) -> bool:
    return type(value) is bytes and len(value) == HASH_LENGTH


def _require_hash(value: object, what: str) -> bytes:
    if not _is_hash(value):
        raise MerkleError(f"{what} must be exactly {HASH_LENGTH} bytes")
    assert isinstance(value, bytes)
    return value


def leaf_hash(data: bytes) -> bytes:
    return sha256(b"\x00" + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return sha256(b"\x01" + _require_hash(left, "left child") + _require_hash(right, "right child"))


def _split(n: int) -> int:
    """The largest power of two strictly less than n (for n >= 2)."""
    return 1 << ((n - 1).bit_length() - 1)


class MerkleTree:
    """An append-only tree over leaf hashes.

    Hashes of perfect subtrees never change once complete, so they are cached; a proof then costs O(log n) cached
    lookups after the first computation.
    """

    def __init__(self, leaf_hashes: Iterable[bytes] = ()) -> None:
        self._leaves: list[bytes] = []
        self._perfect: dict[tuple[int, int], bytes] = {}
        for item in leaf_hashes:
            self.append_leaf_hash(item)

    @property
    def size(self) -> int:
        return len(self._leaves)

    def append_leaf_hash(self, value: bytes) -> int:
        """Append an already-computed leaf hash; returns its index."""
        self._leaves.append(_require_hash(value, "leaf hash"))
        return len(self._leaves) - 1

    def append(self, data: bytes) -> int:
        """Append record bytes (hashed as a leaf); returns the index."""
        return self.append_leaf_hash(leaf_hash(data))

    def leaf_hash_at(self, index: int) -> bytes:
        if not 0 <= index < len(self._leaves):
            raise MerkleError("leaf index out of range")
        return self._leaves[index]

    def _range_hash(self, start: int, end: int) -> bytes:
        """MTH of the leaves ``[start, end)``."""
        count = end - start
        if count == 0:
            return EMPTY_ROOT
        if count == 1:
            return self._leaves[start]
        perfect = count & (count - 1) == 0 and start % count == 0
        if perfect and (start, count) in self._perfect:
            return self._perfect[(start, count)]
        k = _split(count)
        value = node_hash(self._range_hash(start, start + k), self._range_hash(start + k, end))
        if perfect:
            self._perfect[(start, count)] = value
        return value

    def _checked_size(self, size: int | None) -> int:
        if size is None:
            return len(self._leaves)
        if not 0 <= size <= len(self._leaves):
            raise MerkleError("tree size out of range")
        return size

    def root(self, size: int | None = None) -> bytes:
        """The root hash of the first ``size`` leaves (default: all)."""
        return self._range_hash(0, self._checked_size(size))

    def inclusion_proof(self, index: int, size: int | None = None) -> list[bytes]:
        """RFC 9162 section 2.1.3.1 PATH(index, D[0:size])."""
        n = self._checked_size(size)
        if not 0 <= index < n:
            raise MerkleError("leaf index out of range")
        return self._path(index, 0, n)

    def _path(self, m: int, start: int, end: int) -> list[bytes]:
        n = end - start
        if n == 1:
            return []
        k = _split(n)
        if m < k:
            return [*self._path(m, start, start + k), self._range_hash(start + k, end)]
        return [*self._path(m - k, start + k, end), self._range_hash(start, start + k)]

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]:
        """RFC 9162 section 2.1.4.1 PROOF(first, D[0:second])."""
        n = self._checked_size(second)
        if not 0 < first <= n:
            raise MerkleError("first size out of range")
        return self._subproof(first, 0, n, True)

    def _subproof(self, m: int, start: int, end: int, complete: bool) -> list[bytes]:
        n = end - start
        if m == n:
            return [] if complete else [self._range_hash(start, end)]
        k = _split(n)
        if m <= k:
            return [*self._subproof(m, start, start + k, complete), self._range_hash(start + k, end)]
        return [*self._subproof(m - k, start + k, end, False), self._range_hash(start, start + k)]


def _valid_path(path: object) -> bool:
    return isinstance(path, (list, tuple)) and all(_is_hash(item) for item in path)


def verify_inclusion(leaf: bytes, index: int, size: int, path: list[bytes], root: bytes) -> bool:
    """RFC 9162 section 2.1.3.2. ``size`` and ``root`` must come from a verified checkpoint."""
    if not (_is_hash(leaf) and _is_hash(root) and _valid_path(path)):
        return False
    if type(index) is not int or type(size) is not int or not 0 <= index < size:
        return False
    fn, sn, computed = index, size - 1, leaf
    for step in path:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            computed = node_hash(step, computed)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            computed = node_hash(computed, step)
        fn >>= 1
        sn >>= 1
    return sn == 0 and computed == root


def verify_consistency(first: int, second: int, path: list[bytes], first_root: bytes, second_root: bytes) -> bool:
    """RFC 9162 section 2.1.4.2: the tree of size ``second`` extends the tree of size ``first``.

    A first size of zero is refused: the product never publishes a checkpoint for an empty log.
    """
    if not (_is_hash(first_root) and _is_hash(second_root) and _valid_path(path)):
        return False
    if type(first) is not int or type(second) is not int or not 0 < first <= second:
        return False
    if first == second:
        return len(path) == 0 and first_root == second_root
    if len(path) == 0:
        return False
    nodes = list(path)
    if first & (first - 1) == 0:
        nodes.insert(0, first_root)
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    first_computed = second_computed = nodes[0]
    for step in nodes[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            first_computed = node_hash(step, first_computed)
            second_computed = node_hash(step, second_computed)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            second_computed = node_hash(second_computed, step)
        fn >>= 1
        sn >>= 1
    return first_computed == first_root and second_computed == second_root and sn == 0
