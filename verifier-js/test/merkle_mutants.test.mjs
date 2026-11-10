import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { mutant } from "./mutate.mjs";

const fixture = JSON.parse(readFileSync(fileURLToPath(new URL("../../tests/vectors/merkle_reference.json", import.meta.url)), "utf8"));
const hex = (s) => Buffer.from(s, "hex");

// Number of fixture cases (plus the CT known answers and degenerate inputs) on which the module deviates.
function deviations(mod) {
  let bad = 0;
  const zero = Buffer.alloc(32);
  const tree = new mod.MerkleTree();
  fixture.ct.leaves.forEach((leaf, n) => {
    tree.append(hex(leaf));
    if (tree.root().toString("hex") !== fixture.ct.roots[n + 1]) bad++;
  });
  for (const c of fixture.cases) {
    const got =
      c.kind === "inclusion"
        ? mod.verifyInclusion(hex(c.leaf), c.index, c.size, c.path.map(hex), hex(c.root))
        : mod.verifyConsistency(c.first, c.second, c.path.map(hex), hex(c.first_root), hex(c.second_root));
    if (got !== c.go_ok) bad++;
  }
  if (mod.verifyInclusion(Buffer.alloc(0), 0, 1, [], Buffer.alloc(0))) bad++;
  if (mod.verifyConsistency(1, 1, [], Buffer.alloc(0), Buffer.alloc(0))) bad++;
  if (mod.verifyInclusion(zero, 1, 1, [], zero)) bad++;
  return bad;
}

const MUTATIONS = {
  "leaf prefix equals node prefix": ["sha256(Buffer.from([0]), data)", "sha256(Buffer.from([1]), data)"],
  "hash length not enforced": ["Buffer.isBuffer(v) && v.length === HASH_LENGTH", "Buffer.isBuffer(v)"],
  "inclusion end check dropped": ["return sn === 0 && computed.equals(root);", "return computed.equals(root);"],
  "inclusion index bound dropped": ["!isCount(size) || index >= size", "!isCount(size)"],
  "consistency end check dropped": [
    "return firstComputed.equals(firstRoot) && secondComputed.equals(secondRoot) && sn === 0;",
    "return firstComputed.equals(firstRoot) && secondComputed.equals(secondRoot);",
  ],
  "consistency first root not checked": [
    "return firstComputed.equals(firstRoot) && secondComputed.equals(secondRoot) && sn === 0;",
    "return secondComputed.equals(secondRoot) && sn === 0;",
  ],
  "consistency power-of-two prepend dropped": ["if (isPowerOfTwo(first)) nodes.unshift(firstRoot);", ""],
  "equal sizes accept a path": ["return path.length === 0 && firstRoot.equals(secondRoot);", "return firstRoot.equals(secondRoot);"],
};

test("the real module has no deviations", async () => {
  assert.equal(deviations(await import("../src/merkle.mjs")), 0);
});

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`broken rule is detected: ${name}`, async () => {
    const mod = await mutant("../src/merkle.mjs", oldText, newText);
    assert.ok(deviations(mod) > 0, `mutant not detected: ${name}`);
  });
}
