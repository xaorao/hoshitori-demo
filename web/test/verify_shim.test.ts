// The verifier of verifier-js, run on the stand-ins the page uses (pure-JS crypto, files in memory), against the same verifier on node:crypto
// and real files: every recorded bundle case must get the same verdict. The cases are the ones the verifier's own tests replay: honest bundles
// with real time-stamp tokens and certificate chains, and the attacks on them.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { gunzipSync } from "node:zlib";
import { parseRoot } from "../../verifier-js/src/trusted_root.mjs";
import { verifyBundle } from "../../verifier-js/src/verify.mjs";

type Case = { name: string; directories: string[]; files: Record<string, string>; root: string | null; status?: string };

const cases: Case[] = JSON.parse(
  gunzipSync(readFileSync(new URL("../../verifier-js/test/vectors/bundle_cases.json.gz", import.meta.url))).toString("utf8"),
).cases;

const scrub = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(scrub);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).filter(([k]) => k !== "verifier" && k !== "detail").map(([k, v]) => [k, scrub(v)]));
  }
  return value;
};

function real(c: Case): unknown {
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-shim-"));
  for (const d of c.directories) mkdirSync(join(dir, d), { recursive: true });
  for (const [path, content] of Object.entries(c.files)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), Buffer.from(content, "base64"));
  }
  const root = c.root === null ? null : parseRoot(Buffer.from(c.root, "base64"));
  return scrub(JSON.parse(JSON.stringify(verifyBundle(dir, root, c.status))));
}

const shimmed: { name: string; verdict: unknown }[] = JSON.parse(
  execFileSync(process.execPath, ["--import", "./tools/shim_register.mjs", "tools/shim_run.mjs"], { cwd: new URL("..", import.meta.url), maxBuffer: 1 << 28 }).toString("utf8"),
);

test("the recorded bundle cases are all there", () => {
  assert.equal(shimmed.length, cases.length);
  assert.ok(cases.length >= 40, `only ${cases.length} cases`);
  assert.ok(cases.some((c) => c.name === "honest"));
});

for (const [i, c] of cases.entries()) {
  test(`bundle case ${c.name}: the same verdict on the page's crypto and files`, () => {
    const got = shimmed[i];
    assert.ok(got !== undefined && got.name === c.name);
    assert.deepEqual(scrub(got.verdict), real(c));
  });
}

test("the honest bundle reaches C3 on the stand-ins", () => {
  const honest = shimmed.find((s) => s.name === "honest");
  assert.ok(honest !== undefined);
  const verdict = honest.verdict as { timing: { level: string }; integrity: { ok: boolean } };
  assert.equal(verdict.integrity.ok, true);
  assert.equal(verdict.timing.level, "C3");
});
