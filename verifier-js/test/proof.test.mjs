import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";

const cases = loadCases("proof_cases");

export function answer(mod, c) {
  try {
    const p = mod.parseProof(Buffer.from(c.data, "base64"));
    return { ok: true, index: p.index.toString(), path: p.path.map((h) => h.toString("hex")), note: p.note.toString("base64"), extra: p.extra === null ? null : p.extra.toString("base64") };
  } catch (error) {
    if (error instanceof mod.ProofError) return { ok: false };
    return { ok: false, internal: String(error.message) };
  }
}

export const deviations = (mod) => cases.filter((c) => JSON.stringify(answer(mod, c)) !== JSON.stringify(c.python)).length;

test("every recorded proof text gets the answer the Python reader gave", async () => {
  assert.equal(deviations(await import("../src/tlog_proof.mjs")), 0);
});

test("the cases include readable and unreadable proofs", () => {
  assert.ok(cases.some((c) => c.python.ok));
  assert.ok(cases.filter((c) => !c.python.ok).length > 15);
});
