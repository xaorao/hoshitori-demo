import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";

const cases = loadCases("timing_cases");

function rulesOf(r) {
  return {
    operators: new Map(Object.entries(r.operators).map(([name, [parent, ev]]) => [name, { name, parent, evidence: ev === null ? null : { kind: ev[0], ref: ev[1], subjectSha256: ev[2] } }])),
    evidenceKindsAccepted: new Set(r.kinds), requiredIndependent: r.required, windowBeforeCommitS: r.before, windowAfterCommitS: r.after, maxSpreadS: r.spread, uConstantS: r.u,
  };
}

export function answer(mod, c) {
  const tokens = c.tokens.map((t) => ({
    name: t.name, reason: t.reason,
    verified: t.verified === null ? null : { genTimeMs: t.verified.gen_ms, operator: t.verified.operator, rootFingerprint: t.verified.root, signerFingerprint: t.verified.signer },
  }));
  const r = mod.assess(c.committed_ms, c.window_ms, tokens, rulesOf(c.rules));
  return {
    tokens: r.tokens.map((t) => [t.name, t.valid, t.reason, t.genTimeMs, t.rootFingerprint, t.signerFingerprint, t.operator, t.parent, t.timely]),
    independent: r.independentOperators, chosen: [...r.chosen].sort(), spread_ms: r.spreadMs, attested_ms: r.attestedAtMs, level: r.level,
    eligible_ms: r.eligibleAfterOffsetMs, declared: r.windowDeclared, assurance: r.assuranceClass,
  };
}

export const deviations = (mod) =>
  cases.filter((c) => {
    try {
      return JSON.stringify(answer(mod, c)) !== JSON.stringify(c.python);
    } catch {
      return true; // a crash is a difference
    }
  }).length;

test("every recorded assessment gets the answer the Python implementation gave", async () => {
  assert.equal(deviations(await import("../src/timing.mjs")), 0);
});

test("the cases include both levels", () => {
  assert.ok(cases.some((c) => c.python.level === "C3"));
  assert.ok(cases.some((c) => c.python.level === "C1"));
});

// A record's own times must be in a possible order: as_of <= created_at <= committed_at (contract section 2), and a
// token that precedes the record's created_at is an impossible ordering (VERIFICATION_SPEC section 7), each within the clock-offset
// limit that opens the counting window (windowBeforeCommitS, 1 s). The same cases as tests/timing_checks.py `time_order`; the recorded
// corpus has no creation times, and the recorded bundle cases cover these rules end to end.
const COMMIT = Date.UTC(2026, 9, 1, 12, 0, 0);
const operatorOf = (name) => ({ name, parent: `Organisation ${name}`, evidence: { kind: "ccadb_ca_owner_of_record", ref: `record ${name}`, subjectSha256: `root-${name}` } });
const orderRules = (before = 1) => ({
  operators: new Map(["a", "b", "c"].map((n) => [n, operatorOf(n)])),
  evidenceKindsAccepted: new Set(["ccadb_ca_owner_of_record", "operator_published_root"]),
  requiredIndependent: 2, windowBeforeCommitS: before, windowAfterCommitS: 60, maxSpreadS: 5, uConstantS: 5,
});
const stamp = (name, operator, offsetS) => ({ name, reason: null, verified: { genTimeMs: COMMIT + offsetS * 1000, operator, rootFingerprint: `root-${operator}`, signerFingerprint: `signer-${name}` } });

export function orderDeviations(mod) {
  const wrong = [];
  const pair = (first = 1, second = 3) => [stamp("ta", "a", first), stamp("tb", "b", second)];
  const run = (createdS, tokens = pair(), rules = orderRules()) => mod.assess(COMMIT, COMMIT + 300000, tokens, rules, createdS === null ? null : COMMIT + Math.round(createdS * 1000));
  const expect = (label, result, level, error, timely = null) => {
    const got = Object.fromEntries(result.tokens.map((t) => [t.name, t.timely]));
    if (result.level !== level || result.error !== error || (timely !== null && JSON.stringify(got) !== JSON.stringify(timely))) {
      wrong.push(`${label}: level ${result.level}, error ${result.error}, timely ${JSON.stringify(got)}`);
    }
  };
  if (mod.IMPOSSIBLE_ORDER !== "IMPOSSIBLE_ORDER") wrong.push("the code of an impossible order is not IMPOSSIBLE_ORDER");
  const impossible = "IMPOSSIBLE_ORDER";
  expect("no creation time given", run(null), "C3", null);
  expect("no creation time given, the old call", mod.assess(COMMIT, COMMIT + 300000, pair(), orderRules()), "C3", null);
  expect("created before the commit", run(-30), "C3", null);
  expect("created at the commit", run(0), "C3", null);
  expect("created 1 s after the commit", run(1), "C3", null);
  expect("created 1.001 s after the commit", run(1.001), "C1", impossible, { ta: false, tb: false });
  expect("created a day after the commit", run(86400), "C1", impossible, { ta: false, tb: false });
  const late = run(86400);
  if (late.independentOperators !== 0 || late.chosen.length || late.attestedAtMs !== null || !late.tokens.every((t) => t.valid)) wrong.push("an impossible order still counts a token");
  expect("a token 1 s before the creation counts", run(1, pair(0, 3)), "C3", null, { ta: true, tb: true });
  expect("a token 1.001 s before the creation does not", run(1, pair(-0.001, 3)), "C1", null, { ta: false, tb: true });
  if (run(1, pair(-0.001, 3)).independentOperators !== 1) wrong.push("a token before the creation is counted as an operator");
  expect("a token before an early creation still counts in the window", run(-30, pair(-1, 3)), "C3", null);
  expect("no offset allowed: created at the commit", run(0, pair(), orderRules(0)), "C3", null);
  expect("no offset allowed: created 1 ms after the commit", run(0.001, pair(), orderRules(0)), "C1", impossible);
  expect("an offset of 3 s allowed: created 2.5 s after", run(2.5, pair(), orderRules(3)), "C3", null);
  expect("an offset of 3 s allowed: created 3.001 s after", run(3.001, pair(), orderRules(3)), "C1", impossible);
  return wrong;
}

test("a record whose times are in an impossible order gets no counted token", async () => {
  assert.deepEqual(orderDeviations(await import("../src/timing.mjs")), []);
});
