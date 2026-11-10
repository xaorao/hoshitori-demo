// Merkle tree, inclusion and consistency proofs (RFC 9162 section 2.1, SHA-256). Second implementation; the first is
// src/hoshitori/merkle.py. Verification follows the iterative algorithms of sections 2.1.3.2 and 2.1.4.2 and never
// throws on hostile input: a malformed argument is a failed verification. Every hash is exactly 32 bytes.
import { createHash } from "node:crypto";

export const HASH_LENGTH = 32;

export class MerkleError extends Error {}

const sha256 = (...parts) => {
  const h = createHash("sha256");
  for (const part of parts) h.update(part);
  return h.digest();
};

export const EMPTY_ROOT = sha256(Buffer.alloc(0));
const isHash = (v) => Buffer.isBuffer(v) && v.length === HASH_LENGTH;
const requireHash = (v, what) => {
  if (!isHash(v)) throw new MerkleError(`${what} must be exactly ${HASH_LENGTH} bytes`);
  return v;
};

export const leafHash = (data) => sha256(Buffer.from([0]), data);
export const nodeHash = (left, right) =>
  sha256(Buffer.from([1]), requireHash(left, "left child"), requireHash(right, "right child"));

// Largest power of two strictly below n (n >= 2). The generating class is limited to 2**31 - 1 leaves (32-bit
// operations); the verifier functions below have no such limit.
const MAX_TREE_SIZE = 2 ** 31 - 1;
const split = (n) => 1 << (31 - Math.clz32(n - 1));

export class MerkleTree {
  #leaves = [];
  #perfect = new Map();

  constructor(leafHashes = []) {
    for (const h of leafHashes) this.appendLeafHash(h);
  }

  get size() {
    return this.#leaves.length;
  }

  appendLeafHash(value) {
    if (this.#leaves.length >= MAX_TREE_SIZE) throw new MerkleError("tree is full");
    this.#leaves.push(requireHash(value, "leaf hash"));
    return this.#leaves.length - 1;
  }

  append(data) {
    return this.appendLeafHash(leafHash(data));
  }

  #range(start, end) {
    const count = end - start;
    if (count === 0) return EMPTY_ROOT;
    if (count === 1) return this.#leaves[start];
    const perfect = isPowerOfTwo(count) && start % count === 0;
    const key = `${start}:${count}`;
    if (perfect && this.#perfect.has(key)) return this.#perfect.get(key);
    const k = split(count);
    const value = nodeHash(this.#range(start, start + k), this.#range(start + k, end));
    if (perfect) this.#perfect.set(key, value);
    return value;
  }

  #size(size) {
    if (size === undefined) return this.#leaves.length;
    if (!Number.isInteger(size) || size < 0 || size > this.#leaves.length) throw new MerkleError("tree size out of range");
    return size;
  }

  root(size) {
    return this.#range(0, this.#size(size));
  }

  inclusionProof(index, size) {
    const n = this.#size(size);
    if (!Number.isInteger(index) || index < 0 || index >= n) throw new MerkleError("leaf index out of range");
    const path = (m, start, end) => {
      const count = end - start;
      if (count === 1) return [];
      const k = split(count);
      if (m < k) return [...path(m, start, start + k), this.#range(start + k, end)];
      return [...path(m - k, start + k, end), this.#range(start, start + k)];
    };
    return path(index, 0, n);
  }

  consistencyProof(first, second) {
    const n = this.#size(second);
    if (!Number.isInteger(first) || first <= 0 || first > n) throw new MerkleError("first size out of range");
    const sub = (m, start, end, complete) => {
      const count = end - start;
      if (m === count) return complete ? [] : [this.#range(start, end)];
      const k = split(count);
      if (m <= k) return [...sub(m, start, start + k, complete), this.#range(start + k, end)];
      return [...sub(m - k, start + k, end, false), this.#range(start, start + k)];
    };
    return sub(first, 0, n, true);
  }
}

const validPath = (path) => Array.isArray(path) && path.every(isHash);
const isCount = (v) => Number.isSafeInteger(v) && v >= 0;
const isPowerOfTwo = (n) => BigInt(n) > 0n && (BigInt(n) & (BigInt(n) - 1n)) === 0n;

export function verifyInclusion(leaf, index, size, path, root) {
  if (!(isHash(leaf) && isHash(root) && validPath(path))) return false;
  if (!isCount(index) || !isCount(size) || index >= size) return false;
  let fn = index;
  let sn = size - 1;
  let computed = leaf;
  for (const step of path) {
    if (sn === 0) return false;
    if (fn % 2 === 1 || fn === sn) {
      computed = nodeHash(step, computed);
      if (fn % 2 === 0) {
        while (fn % 2 === 0 && fn !== 0) {
          fn = Math.floor(fn / 2);
          sn = Math.floor(sn / 2);
        }
      }
    } else {
      computed = nodeHash(computed, step);
    }
    fn = Math.floor(fn / 2);
    sn = Math.floor(sn / 2);
  }
  return sn === 0 && computed.equals(root);
}

export function verifyConsistency(first, second, path, firstRoot, secondRoot) {
  if (!(isHash(firstRoot) && isHash(secondRoot) && validPath(path))) return false;
  if (!isCount(first) || !isCount(second) || !(first > 0 && first <= second)) return false;
  if (first === second) return path.length === 0 && firstRoot.equals(secondRoot);
  if (path.length === 0) return false;
  const nodes = [...path];
  if (isPowerOfTwo(first)) nodes.unshift(firstRoot);
  let fn = first - 1;
  let sn = second - 1;
  while (fn % 2 === 1) {
    fn = Math.floor(fn / 2);
    sn = Math.floor(sn / 2);
  }
  let firstComputed = nodes[0];
  let secondComputed = nodes[0];
  for (const step of nodes.slice(1)) {
    if (sn === 0) return false;
    if (fn % 2 === 1 || fn === sn) {
      firstComputed = nodeHash(step, firstComputed);
      secondComputed = nodeHash(step, secondComputed);
      if (fn % 2 === 0) {
        while (fn % 2 === 0 && fn !== 0) {
          fn = Math.floor(fn / 2);
          sn = Math.floor(sn / 2);
        }
      }
    } else {
      secondComputed = nodeHash(secondComputed, step);
    }
    fn = Math.floor(fn / 2);
    sn = Math.floor(sn / 2);
  }
  return firstComputed.equals(firstRoot) && secondComputed.equals(secondRoot) && sn === 0;
}
