// Differential-test adapter. One JSON object per line on stdin; one answer per line on stdout.
//   {"op":"tree","leaves":[hex...],"queries":[{"index":i,"size":n},...],"consistency":[{"first":a,"second":b},...]}
//   {"op":"verify_inclusion","leaf":hex,"index":i,"size":n,"path":[hex...],"root":hex}
//   {"op":"verify_consistency","first":a,"second":b,"path":[hex...],"first_root":hex,"second_root":hex}
import { createInterface } from "node:readline";
import { MerkleTree, verifyConsistency, verifyInclusion } from "./merkle.mjs";

const hex = (s) => Buffer.from(s, "hex");
const reader = createInterface({ input: process.stdin });
for await (const line of reader) {
  if (!line.trim()) continue;
  const q = JSON.parse(line);
  if (q.op === "tree") {
    const tree = new MerkleTree(q.leaves.map(hex));
    console.log(
      JSON.stringify({
        roots: Array.from({ length: tree.size + 1 }, (_, n) => tree.root(n).toString("hex")),
        inclusion: q.queries.map((x) => tree.inclusionProof(x.index, x.size).map((p) => p.toString("hex"))),
        consistency: q.consistency.map((x) => tree.consistencyProof(x.first, x.second).map((p) => p.toString("hex"))),
      }),
    );
  } else if (q.op === "verify_inclusion") {
    console.log(JSON.stringify({ ok: verifyInclusion(hex(q.leaf), q.index, q.size, q.path.map(hex), hex(q.root)) }));
  } else {
    console.log(JSON.stringify({ ok: verifyConsistency(q.first, q.second, q.path.map(hex), hex(q.first_root), hex(q.second_root)) }));
  }
}
