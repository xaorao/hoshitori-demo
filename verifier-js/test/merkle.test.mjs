import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { EMPTY_ROOT, MerkleError, MerkleTree, leafHash, verifyConsistency, verifyInclusion } from "../src/merkle.mjs";

const fixture = JSON.parse(readFileSync(fileURLToPath(new URL("../../tests/vectors/merkle_reference.json", import.meta.url)), "utf8"));
const hex = (s) => Buffer.from(s, "hex");
const leafData = (i) => createHash("sha256").update("hoshitori-merkle").update(Buffer.from([(i >>> 24) & 255, (i >>> 16) & 255, (i >>> 8) & 255, i & 255])).digest();

test("Certificate Transparency reference tree: roots and proofs", () => {
  const { ct } = fixture;
  assert.equal(EMPTY_ROOT.toString("hex"), ct.roots[0]);
  const tree = new MerkleTree();
  ct.leaves.forEach((leaf, n) => {
    tree.append(hex(leaf));
    assert.equal(tree.root().toString("hex"), ct.roots[n + 1]);
  });
  for (const item of ct.inclusion) {
    assert.deepEqual(tree.inclusionProof(item.index, item.size).map((p) => p.toString("hex")), item.path);
  }
  for (const item of ct.consistency) {
    assert.deepEqual(tree.consistencyProof(item.first, item.second).map((p) => p.toString("hex")), item.path);
  }
});

test("verdicts agree with the Go reference library on every fixture case", () => {
  let accepted = 0;
  for (const c of fixture.cases) {
    const got =
      c.kind === "inclusion"
        ? verifyInclusion(hex(c.leaf), c.index, c.size, c.path.map(hex), hex(c.root))
        : verifyConsistency(c.first, c.second, c.path.map(hex), hex(c.first_root), hex(c.second_root));
    assert.equal(got, c.go_ok, `case ${c.id} (${c.kind})`);
    if (got) accepted++;
  }
  assert.ok(accepted > 600);
});

test("every proof of every tree up to 70 leaves verifies", () => {
  const tree = new MerkleTree();
  for (let i = 0; i < 70; i++) tree.append(leafData(i));
  for (let size = 1; size <= 70; size++) {
    const root = tree.root(size);
    for (let index = 0; index < size; index++) {
      assert.ok(verifyInclusion(leafHash(leafData(index)), index, size, tree.inclusionProof(index, size), root));
    }
    for (let first = 1; first <= size; first++) {
      assert.ok(verifyConsistency(first, size, tree.consistencyProof(first, size), tree.root(first), root));
    }
  }
});

test("hostile arguments are a failed verification, never an exception", () => {
  const zero = Buffer.alloc(32);
  for (const args of [
    [Buffer.alloc(0), 0, 1, [], zero],
    [zero, 0, 1, [], Buffer.alloc(0)],
    [zero, -1, 1, [], zero],
    [zero, 1, 1, [], zero],
    [zero, 0, 0, [], zero],
    [zero, 0, 2, [Buffer.alloc(31)], zero],
    [zero, 0, 2, ["text"], zero],
    ["text", 0, 1, [], zero],
    [zero, 0.5, 1, [], zero],
    [zero, 0, 1, null, zero],
  ]) {
    assert.equal(verifyInclusion(...args), false);
  }
  assert.equal(verifyConsistency(0, 5, [], zero, zero), false);
  assert.equal(verifyConsistency(2, 1, [], zero, zero), false);
  assert.equal(verifyConsistency(1, 1, [], Buffer.alloc(0), Buffer.alloc(0)), false);
  assert.equal(verifyConsistency(1, 1, [], zero, zero), true);
  assert.throws(() => new MerkleTree().appendLeafHash(Buffer.alloc(5)), MerkleError);
});
