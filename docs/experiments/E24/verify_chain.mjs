// E24 second implementation of the trusted-root client update (see rootchain.py). Reads a case file, prints one JSON result. Not product code.
import { readFileSync } from "node:fs";
import { createPublicKey, verify } from "node:crypto";
const SPKI = Buffer.from("302a300506032b6570032100", "hex");
const MAX_CHAIN = 16;
const unb64 = (s) => Buffer.from(s, "base64");

function parse(payload) {
  const d = JSON.parse(payload.toString("utf8"));
  if (d.type !== "hoshitori-trusted-root" || !Number.isInteger(d.version) || !Number.isInteger(d.expires)) throw new Error("shape");
  return d;
}
function sigOk(pubB64, sigB64, payload) {
  try {
    const key = createPublicKey({ key: Buffer.concat([SPKI, unb64(pubB64)]), format: "der", type: "spki" });
    return verify(null, payload, key, unb64(sigB64));
  } catch { return false; }
}
function countValid(root, sigs, payload) {
  const seen = new Set();
  for (const s of sigs) {
    const kid = s.keyid;
    if (seen.has(kid) || !(kid in root.root_keys)) continue;
    if (sigOk(root.root_keys[kid], s.sig_b64 ?? "", payload)) seen.add(kid);
  }
  return seen.size;
}
function update(trusted, channels, now, maxChain = MAX_CHAIN) {
  let cur = parse(unb64(trusted.payload_b64)); let steps = 0; let single = false;
  const out = (status, code) => ({ status, version: cur.version, code, single_channel: single });
  for (;;) {
    const n = cur.version + 1;
    const cands = channels.filter((ch) => String(n) in ch).map((ch) => ch[String(n)]);
    if (cands.length === 0) break;
    if (steps >= maxChain) return out("REJECTED", "CHAIN_BOUND");
    if (new Set(cands.map((c) => c.payload_b64)).size > 1) return out("FORK", "FORK");
    const env = cands[0]; single = cands.length === 1 && channels.length > 1;
    let payload, next;
    try { payload = unb64(env.payload_b64); next = parse(payload); } catch { return out("REJECTED", "MALFORMED"); }
    if (countValid(cur, env.signatures, payload) < cur.root_threshold) return out("REJECTED", "OLD_THRESHOLD");
    if (countValid(next, env.signatures, payload) < next.root_threshold) return out("REJECTED", "NEW_THRESHOLD");
    if (next.version !== n) return out("REJECTED", "VERSION_STEP");
    cur = next; steps += 1;
  }
  const res = cur.expires <= now ? out("EXPIRED", "EXPIRED") : out("CURRENT", "OK");
  res.root = cur; return res;
}
function eraAccepts(root, keyid, size) {
  for (const e of root.log_eras) if (e.keyid === keyid) {
    if (size < e.from_size) return "REJECT_BEFORE_ERA";
    if (e.through_size !== null && size > e.through_size) return "REJECT_BEYOND_CLOSING_SIZE";
    return "ACCEPT";
  }
  return "UNKNOWN_ERA";
}
const c = JSON.parse(readFileSync(process.argv[2], "utf8"));
const r = update(c.trusted, c.channels, c.now, c.max_chain ?? MAX_CHAIN);
const root = r.root; delete r.root;
r.era = (c.queries ?? []).map(([k, s]) => (root ? eraAccepts(root, k, s) : "NO_ROOT"));
process.stdout.write(JSON.stringify(r));
