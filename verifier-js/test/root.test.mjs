import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";

const cases = loadCases("root_cases");

function facts(root) {
  const t = root.timeStamp;
  return {
    version: root.version, expires_ms: root.expires, threshold: root.rootThreshold, keys: [...root.rootKeys.keys()].sort(), origin: root.origin,
    eras: root.eras.map((e) => [e.key.name, e.key.keyId.toString("hex"), e.fromSize, e.throughSize]),
    pinned: t.pinned.map((p) => [p.sha256, p.operator, p.der !== null]),
    operators: Object.fromEntries([...t.operators].sort(([a], [b]) => (a < b ? -1 : 1)).map(([n, o]) => [n, [o.parent, o.evidence === null ? null : [o.evidence.kind, o.evidence.ref, o.evidence.subjectSha256]]])),
    kinds: [...t.evidenceKindsAccepted].sort(),
    rules: [t.requiredIndependent, t.windowBeforeCommitS, t.windowAfterCommitS, t.closeAfterCommitS, t.maxSpreadS, t.uConstantS],
    witnesses: [root.witnessesRequired, root.witnessKeys.map((k) => k.keyId.toString("hex"))],
    mode: root.scoredRequiresDataMode, sha256: root.sha256,
  };
}

// The answer a module gives for one recorded query, in the shape the Python verifier recorded.
export function answer(mod, q) {
  try {
    if (q.op === "parse") return { ok: true, facts: facts(mod.parseRoot(Buffer.from(q.payload, "base64"))) };
    const options = { floorVersion: q.floor };
    if (q.max_chain !== null) options.maxChain = q.max_chain;
    const r = mod.update(mod.parseRoot(Buffer.from(q.initial, "base64")), q.channels, q.now_ms, options);
    return { status: r.status, code: r.code, version: r.root.version, sha256: r.root.sha256, single: r.singleChannel };
  } catch (error) {
    if (error instanceof mod.RootError) return { ok: false, code: error.code };
    return { ok: false, code: "INTERNAL" };
  }
}

export function deviations(mod) {
  let bad = 0;
  for (const q of cases) if (JSON.stringify(answer(mod, q)) !== JSON.stringify(q.python)) bad++;
  return bad;
}

test("every recorded document and update chain gets the answer the Python implementation gave", async () => {
  const mod = await import("../src/trusted_root.mjs");
  const wrong = [];
  cases.forEach((q, i) => {
    const got = answer(mod, q);
    if (JSON.stringify(got) !== JSON.stringify(q.python)) wrong.push(`#${i} ${q.op}: expected ${JSON.stringify(q.python).slice(0, 160)} got ${JSON.stringify(got).slice(0, 160)}`);
  });
  assert.deepEqual(wrong, []);
});

test("the cases cover updates and parsing, acceptance and every rejection code", () => {
  const codes = new Set(cases.filter((q) => q.op === "update").map((q) => q.python.code));
  for (const code of ["OK", "EXPIRED", "FORK", "MALFORMED", "OLD_THRESHOLD", "NEW_THRESHOLD", "VERSION_STEP", "CHAIN_BOUND", "ROLLBACK"]) assert.ok(codes.has(code), code);
  assert.ok(cases.some((q) => q.op === "parse" && q.python.ok));
  assert.ok(cases.some((q) => q.op === "parse" && !q.python.ok));
});
