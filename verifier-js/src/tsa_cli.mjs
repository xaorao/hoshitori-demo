// Differential-test adapter for the token verifier. One JSON object per line on stdin, one answer per line on stdout:
//   {"token": base64, "digest": hex, "pins": [{"sha256": hex, "operator": text, "der": base64 | null}], "nonce": decimal text | null}
import { createInterface } from "node:readline";
import { PinnedRoot, TokenError, TokenPolicy, verifyToken } from "./tsa.mjs";

const reader = createInterface({ input: process.stdin });
for await (const line of reader) {
  if (!line.trim()) continue;
  const q = JSON.parse(line);
  try {
    const pins = q.pins.map((p) => new PinnedRoot(p.sha256, p.operator, p.der === null ? null : Buffer.from(p.der, "base64")));
    const r = verifyToken(Buffer.from(q.token, "base64"), Buffer.from(q.digest, "hex"), new TokenPolicy(pins), q.nonce === null ? null : BigInt(q.nonce));
    console.log(JSON.stringify({ ok: true, gen_ms: r.genTimeMs, root: r.rootFingerprint, signer: r.signerFingerprint, operator: r.operator, serial: r.serial, policy: r.policyOid, path: r.pathFingerprints }));
  } catch (error) {
    console.log(JSON.stringify({ ok: false, code: error instanceof TokenError ? error.code : "INTERNAL", message: String(error.message).slice(0, 200) }));
  }
}
