// Run the verifier's recorded bundle cases on the stand-ins (crypto, files, paths) and print the verdicts as JSON.
// Usage: node --import ./tools/shim_register.mjs tools/shim_run.mjs
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { setFiles } from "../src/verify/node_fs.ts";
import { parseRoot } from "../../verifier-js/src/trusted_root.mjs";
import { verifyBundle } from "../../verifier-js/src/verify.mjs";

const cases = JSON.parse(
  gunzipSync(readFileSync(new URL("../../verifier-js/test/vectors/bundle_cases.json.gz", import.meta.url))).toString("utf8"),
).cases;
const out = [];
for (const c of cases) {
  const entries = Object.fromEntries(Object.entries(c.files).map(([p, b64]) => [`/bundle/${p}`, new Uint8Array(Buffer.from(b64, "base64"))]));
  setFiles(entries, c.directories.map((d) => `/bundle/${d}`));
  const root = c.root === null ? null : parseRoot(Buffer.from(c.root, "base64"));
  out.push({ name: c.name, verdict: JSON.parse(JSON.stringify(verifyBundle("/bundle", root, c.status))) });
}
process.stdout.write(JSON.stringify(out));
