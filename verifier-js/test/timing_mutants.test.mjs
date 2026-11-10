import assert from "node:assert/strict";
import { test } from "node:test";
import { mutantTree } from "./mutate.mjs";
import { deviations, orderDeviations } from "./timing.test.mjs";

const MUTATIONS = {
  "the window start is exclusive": ["committedMs - before <= proof.genTimeMs &&", "committedMs - before < proof.genTimeMs &&"],
  "the window end is exclusive": ["proof.genTimeMs <= committedMs + after;", "proof.genTimeMs < committedMs + after;"],
  "the window is ignored": ["let timely = committedMs - before <= proof.genTimeMs && proof.genTimeMs <= committedMs + after;", "let timely = true;"],
  "the outcome window is ignored": ["if (windowOpensMs !== null) timely = timely && proof.genTimeMs + uncertainty < windowOpensMs;", ""],
  "the uncertainty is not subtracted": ["proof.genTimeMs + uncertainty < windowOpensMs", "proof.genTimeMs < windowOpensMs"],
  "the uncertainty boundary is inclusive": ["proof.genTimeMs + uncertainty < windowOpensMs", "proof.genTimeMs + uncertainty <= windowOpensMs"],
  "evidence of any kind is accepted": ["if (!rules.evidenceKindsAccepted.has(proof.kind) || !proof.ref || proof.subjectSha256 !== rootFingerprint) return null;", "if (!proof.ref || proof.subjectSha256 !== rootFingerprint) return null;"],
  "evidence about any root is accepted": ["|| !proof.ref || proof.subjectSha256 !== rootFingerprint) return null;", "|| !proof.ref) return null;"],
  "missing evidence is accepted": ["if (!operator || operator.evidence === null) return null;", "if (!operator) return null;"],
  "signers are not counted": ["return Math.min(new Set(usable.map((t) => t.signerFingerprint)).size, new Set(usable.map((t) => t.parent)).size);", "return new Set(usable.map((t) => t.parent)).size;"],
  "parents are not counted": ["return Math.min(new Set(usable.map((t) => t.signerFingerprint)).size, new Set(usable.map((t) => t.parent)).size);", "return new Set(usable.map((t) => t.signerFingerprint)).size;"],
  "tokens without a parent are counted": ["const usable = tokens.filter((t) => t.parent !== null);", "const usable = tokens;"],
  "the spread is not limited": ["if (ordered[end].genTimeMs - ordered[start].genTimeMs > rules.maxSpreadS * 1000) break;", ""],
  "the spread limit is exclusive": ["> rules.maxSpreadS * 1000) break;", ">= rules.maxSpreadS * 1000) break;"],
  "one independent operator suffices": ["if (independent(subset) >= rules.requiredIndependent) return subset;", "if (independent(subset) >= 1) return subset;"],
  "tokens without evidence carry the level": ["counted.filter((t) => t.genTimeMs !== null && t.parent !== null)", "counted.filter((t) => t.genTimeMs !== null)"],
  "the attestation is the earliest token": ["attestedAtMs: Math.max(...times)", "attestedAtMs: Math.min(...times)"],
  "the eligible offset forgets the spread": ["eligibleAfterOffsetMs: rules.uConstantS * 1000 + spread", "eligibleAfterOffsetMs: rules.uConstantS * 1000"],
  "the eligible offset forgets the uncertainty": ["eligibleAfterOffsetMs: rules.uConstantS * 1000 + spread", "eligibleAfterOffsetMs: spread"],
  "rejected tokens are counted": ["operator: null, parent: null, timely: false };", "operator: null, parent: null, timely: true };"],
  "the declared window flag is wrong": ["const declared = windowOpensMs !== null;", "const declared = true;"],
  "windows may start anywhere in the order": ["a.genTimeMs - b.genTimeMs || (a.name", "(a.name < b.name ? -1 : 1) || (a.name"],
};

test("the real module deviates on no recorded case", async () => {
  assert.equal(deviations(await import("../src/timing.mjs")), 0);
});

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree("src/timing.mjs", oldText, newText);
    assert.ok(deviations(mod) > 0, "the recorded cases do not notice this change");
  });
}

// The order of a record's own times, on the explicit cases of timing.test.mjs.
const ORDER_MUTATIONS = {
  "a record created after its commit is not refused": ["const impossible = createdMs !== null && createdMs > committedMs + before;", "const impossible = false;"],
  "the creation limit is exclusive": ["createdMs > committedMs + before;", "createdMs >= committedMs + before;"],
  "the creation limit ignores the clock offset": ["createdMs > committedMs + before;", "createdMs > committedMs;"],
  "a token may precede the record's creation": ["if (createdMs !== null) timely = timely && createdMs - before <= proof.genTimeMs;", ""],
  "a token may precede the creation by more than the offset": ["timely && createdMs - before <= proof.genTimeMs;", "timely && createdMs - 2 * before <= proof.genTimeMs;"],
  "an impossible order still counts tokens": ["if (impossible) timely = false;", ""],
  "an impossible order is not reported": ["const error = impossible ? IMPOSSIBLE_ORDER : null;", "const error = null;"],
};

for (const [name, [oldText, newText]] of Object.entries(ORDER_MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree("src/timing.mjs", oldText, newText);
    assert.ok(orderDeviations(mod).length > 0, "the order cases do not notice this change");
  });
}
