import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";
import { CODES, PinnedRoot, TokenError, TokenPolicy, verifyToken } from "../src/tsa.mjs";

const cases = loadCases("tsa_cases");

export function run(mod, c) {
  const pins = c.pins.map((p) => new mod.PinnedRoot(p.sha256, p.operator, p.der === null ? null : Buffer.from(p.der, "base64")));
  try {
    const r = mod.verifyToken(Buffer.from(c.token, "base64"), Buffer.from(c.digest, "hex"), new mod.TokenPolicy(pins), c.nonce === null ? null : BigInt(c.nonce));
    return { ok: true, gen_ms: r.genTimeMs, root: r.rootFingerprint, signer: r.signerFingerprint, operator: r.operator, serial: r.serial, policy: r.policyOid, path: r.pathFingerprints };
  } catch (error) {
    if (!(error instanceof mod.TokenError)) return { ok: false, code: "INTERNAL", message: String(error.message) };
    return { ok: false, code: error.code };
  }
}

test("every recorded token gets the answer the Python verifier gave", async () => {
  const mod = { PinnedRoot, TokenError, TokenPolicy, verifyToken };
  const wrong = [];
  cases.forEach((c, i) => {
    const got = run(mod, c);
    // In the bit-flip sweep only acceptance is compared: damage in a part only one implementation reads can change which rule names it.
    const same = c.scenario === "live_tokens_sweep" ? got.ok === c.python.ok && (!got.ok || JSON.stringify(got) === JSON.stringify(c.python)) : JSON.stringify(got) === JSON.stringify(c.python);
    if (!same) wrong.push(`#${i} ${c.scenario}: expected ${JSON.stringify(c.python).slice(0, 120)} got ${JSON.stringify(got).slice(0, 120)}`);
  });
  assert.deepEqual(wrong, []);
});

test("the cases exercise both outcomes and many rejection families", () => {
  const codes = new Set(cases.filter((c) => !c.python.ok).map((c) => c.python.code));
  assert.ok(cases.some((c) => c.python.ok));
  for (const code of ["IMPRINT", "NONCE", "STATUS", "SIGNATURE", "ALGORITHM", "PATH", "CERT", "ATTRIBUTES", "ESS", "SIGNER_ID", "PARSE", "SIZE", "DIGEST", "NO_ROOT"]) {
    assert.ok(codes.has(code), `no case rejected with ${code}`);
  }
  for (const code of codes) assert.ok(CODES.includes(code));
});

test("a pin must be 64 lower-case hex characters and match its certificate", () => {
  assert.throws(() => new PinnedRoot("A".repeat(64), "x"));
  assert.throws(() => new PinnedRoot("a".repeat(63), "x"));
  assert.throws(() => new PinnedRoot("g".repeat(64), "x"));
  assert.throws(() => new PinnedRoot("0".repeat(64), "x", Buffer.from("not the certificate")));
  assert.equal(new PinnedRoot("a".repeat(64), "x").der, null);
});
