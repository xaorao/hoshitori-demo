"""Experiment E2a, implementation A: RFC 9162 section 2.1 Merkle tree, inclusion and consistency proofs (SHA-256).

Written from the RFC text. Generation uses the recursive definitions (PATH, SUBPROOF); verification uses the
iterative algorithms of sections 2.1.3.2 and 2.1.4.2. The two are deliberately different code paths.
"""
from __future__ import annotations

import hashlib


def H(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def leaf_hash(data: bytes) -> bytes:
    return H(b"\x00" + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return H(b"\x01" + left + right)


def _split(n: int) -> int:
    """Largest power of two strictly less than n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def mth(leaf_hashes: list[bytes]) -> bytes:
    n = len(leaf_hashes)
    if n == 0:
        return H(b"")
    if n == 1:
        return leaf_hashes[0]
    k = _split(n)
    return node_hash(mth(leaf_hashes[:k]), mth(leaf_hashes[k:]))


def inclusion_path(m: int, leaf_hashes: list[bytes]) -> list[bytes]:
    n = len(leaf_hashes)
    if not 0 <= m < n:
        raise ValueError("leaf index out of range")
    if n == 1:
        return []
    k = _split(n)
    if m < k:
        return inclusion_path(m, leaf_hashes[:k]) + [mth(leaf_hashes[k:])]
    return inclusion_path(m - k, leaf_hashes[k:]) + [mth(leaf_hashes[:k])]


def _subproof(m: int, leaf_hashes: list[bytes], complete: bool) -> list[bytes]:
    n = len(leaf_hashes)
    if m == n:
        return [] if complete else [mth(leaf_hashes)]
    k = _split(n)
    if m <= k:
        return _subproof(m, leaf_hashes[:k], complete) + [mth(leaf_hashes[k:])]
    return _subproof(m - k, leaf_hashes[k:], False) + [mth(leaf_hashes[:k])]


def consistency_proof(m: int, leaf_hashes: list[bytes]) -> list[bytes]:
    n = len(leaf_hashes)
    if not 0 < m <= n:
        raise ValueError("first size out of range")
    return _subproof(m, leaf_hashes, True)


def verify_inclusion(leaf: bytes, index: int, size: int, path: list[bytes], root: bytes) -> bool:
    if index >= size or index < 0:
        return False
    fn, sn, r = index, size - 1, leaf
    for p in path:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root


def verify_consistency(first: int, second: int, path: list[bytes], first_hash: bytes, second_hash: bytes) -> bool:
    if not 0 < first <= second:
        return False
    if first == second:
        return len(path) == 0 and first_hash == second_hash
    if len(path) == 0:
        return False
    path = list(path)
    if first & (first - 1) == 0:
        path.insert(0, first_hash)
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = node_hash(c, fr)
            sr = node_hash(c, sr)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return fr == first_hash and sr == second_hash and sn == 0
