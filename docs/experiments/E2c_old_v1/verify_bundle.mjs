#!/usr/bin/env node
// Experiment E2c, verifier B (JavaScript). Written separately from verify_bundle.py; same bundle, same four-part verdict.
// The CMS signature math of a time-stamp token is delegated to `openssl ts -verify` (no pure-JS CMS verifier exists in this
// experiment); everything else (checkpoint note, Ed25519, Merkle inclusion, canonical record, policy, assurance) is JavaScript.
import { readFileSync, readdirSync, existsSync, writeFileSync, mkdtempSync } from "node:fs";
import { createHash, createPublicKey, verify as edVerify, X509Certificate } from "node:crypto";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
const HERE = dirname(fileURLToPath(import.meta.url));
const { canonicalizeBytes } = await import(join(HERE, "../E1/canon.mjs"));
const { leafHash, verifyInclusion } = await import(join(HERE, "../E2a/merkle.mjs"));
const OSSL = "/opt/homebrew/bin/openssl";
const sha256 = (b) => createHash("sha256").update(b).digest();
const NOT_PROVEN = ["that the evidence was true or complete", "that the forecast is good", "that the agent reasoned as stored", "who controls the signing key",
  "that no other log history exists beyond the witnessed checkpoints (no witness cosignature is required by this bundle)"];

function b64d(s) { const b = Buffer.from(s, "base64"); if (b.toString("base64") !== s) throw new Error("non-canonical base64"); return b; }
function parseProof(buf) {
  const lines = buf.toString("utf8").split("\n");
  if (lines[0] !== "c2sp.org/tlog-proof@v1") throw new Error("bad proof header");
  let i = 1; if (lines[i].startsWith("extra ")) i++;
  if (!lines[i].startsWith("index ")) throw new Error("missing index");
  const idxS = lines[i].slice(6);
  if (!/^(0|[1-9][0-9]*)$/.test(idxS)) throw new Error("bad index");
  i++; const path = [];
  while (lines[i] !== "") { path.push(b64d(lines[i])); i++; }
  return { index: Number(idxS), path, cp: Buffer.from(lines.slice(i + 1).join("\n"), "utf8") };
}
function parseCheckpoint(cp) {
  const s = cp.toString("utf8"); const k = s.indexOf("\n\n");
  if (k < 0) throw new Error("no signature block");
  const body = s.slice(0, k); const lines = body.split("\n");
  if (lines.length < 3 || !lines.slice(0, 3).every((l) => l.length)) throw new Error("checkpoint needs three non-empty lines");
  if (!/^(0|[1-9][0-9]*)$/.test(lines[1])) throw new Error("bad size");
  const sigs = [];
  for (const ln of s.slice(k + 2).split("\n")) {
    if (!ln) continue; if (!ln.startsWith("— ")) throw new Error("bad signature line");
    const rest = ln.slice(2); const sp = rest.lastIndexOf(" ");
    sigs.push([rest.slice(0, sp), b64d(rest.slice(sp + 1))]);
  }
  return { origin: lines[0], size: Number(lines[1]), root: b64d(lines[2]), noteText: Buffer.from(body + "\n", "utf8"), sigs };
}
function verifyNote(noteText, sigs, vkey, origin) {
  const [name, kidHex, keyB64] = (() => { const a = vkey.split("+"); return [a[0], a[1], a.slice(2).join("+")]; })();
  const km = b64d(keyB64); if (km[0] !== 1 || km.length !== 33 || name !== origin) return false;
  const pub = km.subarray(1);
  const kid = sha256(Buffer.concat([Buffer.from(name), Buffer.from([10, 1]), pub])).subarray(0, 4);
  if (kid.toString("hex") !== kidHex) return false;
  const key = createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), pub]), format: "der", type: "spki" });
  for (const [sname, sb] of sigs) if (sname === name && sb.length === 68 && sb.subarray(0, 4).equals(kid)) return edVerify(null, noteText, key, sb.subarray(4));
  return false;
}
function sh(args, input) { return spawnSync(OSSL, args, { input, env: { PATH: "/usr/bin:/bin" } }); }
function verifyToken(file, digestHex, pinnedSet, pinnedPem) {
  const v = sh(["ts", "-verify", "-in", file, "-digest", digestHex, "-CAfile", pinnedPem, "-partial_chain"]);
  const entry = { file: file.split("/").pop(), valid: v.status === 0, reason: v.status === 0 ? null : String(v.stderr).slice(0, 80) };
  if (!entry.valid) return entry;
  const txt = String(sh(["ts", "-reply", "-in", file, "-text"]).stdout);
  const gen = Date.parse(txt.match(/Time stamp: (.+)/)[1].replace(" GMT", " UTC"));
  const tok = sh(["ts", "-reply", "-in", file, "-token_out"]).stdout;
  const pem = String(sh(["pkcs7", "-inform", "DER", "-print_certs"], tok).stdout);
  const certs = [...pem.matchAll(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g)].map((m) => new X509Certificate(m[0]));
  const fp = (c) => c.fingerprint256.replace(/:/g, "").toLowerCase();
  const signer = certs.find((c) => (c.keyUsage || []).includes("1.3.6.1.5.5.7.3.8"));
  const root = certs.find((c) => pinnedSet.has(fp(c)));
  const cms = String(sh(["cms", "-cmsout", "-print", "-inform", "DER"], tok).stdout);
  const si = cms.slice(cms.indexOf("signerInfos:"));
  const cmsDigest = (si.match(/digestAlgorithm:[\s\S]*?algorithm: (\w+)/) || [])[1];
  if (!signer || !root) { entry.valid = false; entry.reason = "signer or pinned root not found"; return entry; }
  if (!["sha256", "sha384", "sha512"].includes(cmsDigest)) { entry.valid = false; entry.reason = "weak CMS digest " + cmsDigest; return entry; }
  entry.gen = gen / 1000; entry.signer_fp = fp(signer); entry.root_fp = fp(root); entry.cms_digest = cmsDigest;
  return entry;
}
function verify(bundle) {
  const out = { verifier: "javascript", integrity: {}, timing: {}, eligibility: {}, errors: [], not_proven: NOT_PROVEN };
  let policy, commit;
  try { policy = JSON.parse(readFileSync(join(bundle, "policy.json"), "utf8")); commit = JSON.parse(readFileSync(join(bundle, "commit.json"), "utf8")); }
  catch (e) { out.errors.push("policy/commit unreadable: " + String(e.message).slice(0, 80)); return out; }
  const notes = new Set(); const records = {}; out.integrity.items = {};
  const dir = join(bundle, "items");
  for (const f of existsSync(dir) ? readdirSync(dir).filter((n) => n.endsWith(".canonical.json")).sort() : []) {
    const rid = f.split(".")[0]; const res = { canonical: false, inclusion: false };
    try {
      const raw = readFileSync(join(dir, f));
      res.canonical = canonicalizeBytes(raw, "record").equals(raw);
      records[rid] = JSON.parse(raw.toString("utf8"));
      const { index, path, cp } = parseProof(readFileSync(join(dir, rid + ".tlog-proof")));
      const c = parseCheckpoint(cp); notes.add(c.noteText.toString("hex"));
      res.origin_ok = c.origin === policy.log.origin;
      res.signature = verifyNote(c.noteText, c.sigs, policy.log.vkey, c.origin);
      res.inclusion = c.root.length === 32 && path.every((p) => p.length === 32) && verifyInclusion(leafHash(raw), index, c.size, path, c.root);
    } catch (e) { res.error = e.constructor.name + ": " + String(e.message).slice(0, 80); }
    out.integrity.items[rid] = res;
  }
  let chainOk = Object.keys(records).length > 0;
  for (const [rid, r] of Object.entries(records)) {
    if (r.type === "forecast") for (const ev of r.evidence_ids || []) if (!(ev in records)) chainOk = false;
    if (r.type === "evidence") {
      const rp = join(bundle, "raw", "ryo_response.json");
      if (!existsSync(rp) || sha256(readFileSync(rp)).toString("hex") !== r.raw_sha256) chainOk = false;
    }
  }
  out.integrity.evidence_chain = chainOk; out.integrity.one_checkpoint = notes.size === 1;
  out.integrity.ok = Object.keys(records).length > 0 && chainOk && notes.size === 1 && Object.values(out.integrity.items).every((v) => v.canonical && v.inclusion && v.signature && v.origin_ok);
  const toks = [];
  if (notes.size === 1) {
    const noteText = Buffer.from([...notes][0], "hex"); const digestHex = sha256(noteText).toString("hex");
    const pinnedSet = new Set(Object.keys(policy.tsa.pinned_roots_sha256));
    const dirT = mkdtempSync(join(tmpdir(), "e2c-")); const pinnedPem = join(dirT, "pinned.pem");
    writeFileSync(pinnedPem, readFileSync(join(bundle, "tsa_roots.pem")));
    const committed = Date.parse(commit.committed_at) / 1000, opens = Date.parse(commit.outcome_window_opens_at) / 1000, u = policy.u_constant_s;
    const tdir = join(bundle, "tsa");
    for (const f of existsSync(tdir) ? readdirSync(tdir).filter((n) => n.endsWith(".tsr")).sort() : []) {
      const e = verifyToken(join(tdir, f), digestHex, pinnedSet, pinnedPem);
      if (e.valid) { e.timely = Math.abs(e.gen - committed) <= policy.tsa.window_after_commit_s && e.gen < opens - u; }
      toks.push(e);
    }
  }
  const good = toks.filter((t) => t.valid && t.timely);
  const independent = Math.min(new Set(good.map((t) => t.root_fp)).size, new Set(good.map((t) => t.signer_fp)).size);
  const gens = good.map((t) => t.gen).sort((a, b) => a - b); const spread = gens.length ? gens[gens.length - 1] - gens[0] : null;
  const c3 = independent >= policy.tsa.required_independent && spread !== null && spread <= policy.tsa.max_spread_s;
  out.timing = { tokens: toks.map((t) => ({ file: t.file, valid: t.valid, reason: t.reason, signer_fp: t.signer_fp, root_fp: t.root_fp, timely: t.timely })), independent_operators: independent, spread_s: spread,
    level_supported: !out.integrity.ok ? "none (integrity failed)" : c3 ? "C3" : good.length ? "below C3 (fewer than two independent operators)" : "below C3 (no valid timely token)",
    assurance_class: `TA-3 (assumed clock uncertainty u=${policy.u_constant_s}s plus observed spread; not proven)` };
  const modes = new Set(Object.values(records).filter((r) => r.type === "evidence").map((r) => r.data_mode));
  out.eligibility = { data_modes: [...modes].filter(Boolean).sort(), scorable: modes.size === 1 && modes.has(policy.scored_requires_data_mode) };
  return out;
}
console.log(JSON.stringify(verify(process.argv[2]), null, 1));
