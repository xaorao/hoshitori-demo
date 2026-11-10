import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";

const cases = loadCases("bundle_cases");

// Recreate each recorded directory once; the mutants below reuse the same trees.
const trees = cases.map((c) => {
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-bundle-"));
  for (const d of c.directories) mkdirSync(join(dir, d), { recursive: true });
  for (const [path, content] of Object.entries(c.files)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), Buffer.from(content, "base64"));
  }
  return dir;
});

const scrub = (value) => {
  if (Array.isArray(value)) return value.map(scrub);
  if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([k]) => k !== "verifier" && k !== "detail").map(([k, v]) => [k, scrub(v)]));
  return value;
};

export function verdictOf(verifyMod, rootMod, index) {
  const c = cases[index];
  const root = c.root === null ? null : rootMod.parseRoot(Buffer.from(c.root, "base64"));
  return scrub(JSON.parse(JSON.stringify(verifyMod.verifyBundle(trees[index], root, c.status))));
}

// The size limits, lowered for the duration of the check (as the Python scenarios do).
function limitDeviations(verifyMod, rootMod) {
  const honest = cases.findIndex((c) => c.name === "honest");
  const root = rootMod.parseRoot(Buffer.from(cases[honest].root, "base64"));
  const saved = { ...verifyMod.limits };
  let bad = 0;
  try {
    verifyMod.limits.MAX_ITEMS = 1;
    let verdict = JSON.parse(JSON.stringify(verifyMod.verifyBundle(trees[honest], root)));
    if (!verdict.integrity.errors.includes("TOO_MANY_ITEMS") || verdict.integrity.ok || Object.keys(verdict.integrity.items).length !== 1) bad++;
    verifyMod.limits.MAX_ITEMS = saved.MAX_ITEMS;
    verifyMod.limits.MAX_TOKENS = 1;
    verdict = JSON.parse(JSON.stringify(verifyMod.verifyBundle(trees[honest], root)));
    if (JSON.stringify(verdict.timing.tokens.map((t) => t.reason)) !== JSON.stringify([null, "TOO_MANY"]) || verdict.timing.level !== "C1") bad++;
    verifyMod.limits.MAX_TOKENS = saved.MAX_TOKENS;
    verifyMod.limits.MAX_RAW_BYTES = 1;
    verdict = JSON.parse(JSON.stringify(verifyMod.verifyBundle(trees[honest], root)));
    if (verdict.integrity.raw.mismatched.length !== 1 || verdict.integrity.raw.verified.length !== 0) bad++;
  } finally {
    Object.assign(verifyMod.limits, saved);
  }
  const missing = JSON.parse(JSON.stringify(verifyMod.verifyBundle(join(trees[honest], "does-not-exist"), root)));
  if (JSON.stringify(missing.integrity.errors) !== JSON.stringify(["NO_ITEMS"]) || missing.integrity.ok) bad++;
  return bad;
}

export function deviations(verifyMod, rootMod) {
  let bad = limitDeviations(verifyMod, rootMod);
  cases.forEach((c, i) => {
    try {
      if (JSON.stringify(verdictOf(verifyMod, rootMod, i)) !== JSON.stringify(scrub(c.python))) bad++;
    } catch {
      bad++;
    }
  });
  return bad;
}

export const caseNames = cases.map((c) => c.name);

// On a runtime whose Unicode is older than 15.0 there is no verdict at all, whatever the bundle holds, and no root is read (finding
// D-NFC): the error is not a canonical-form failure of the bundle or the root.
export function runtimeDeviations(verifyMod, rootMod, canonicalMod) {
  const honest = cases.findIndex((c) => c.name === "honest");
  const payload = Buffer.from(cases[honest].root, "base64");
  const wrong = [];
  const saved = canonicalMod.runtime.unicodeAtLeast15;
  try {
    canonicalMod.runtime.unicodeAtLeast15 = false;
    for (const [label, call] of [
      ["an honest bundle", () => verifyMod.verifyBundle(trees[honest], null)],
      ["a missing directory", () => verifyMod.verifyBundle(join(trees[honest], "does-not-exist"), null)],
      ["the trusted root", () => rootMod.parseRoot(payload)],
    ]) {
      let got = "<a result>";
      try {
        call();
      } catch (error) {
        got = error instanceof canonicalMod.UnicodeVersionError ? error.message : `<${error.constructor.name}: ${error.message}>`;
      }
      if (got !== "Unicode 15.0 or later is needed to check NFC; this runtime has an older version") wrong.push(`${label}: ${got}`);
    }
  } finally {
    canonicalMod.runtime.unicodeAtLeast15 = saved;
  }
  return wrong;
}

test("on a runtime older than Unicode 15.0 there is no verdict and no root", async () => {
  const wrong = runtimeDeviations(await import("../src/verify.mjs"), await import("../src/trusted_root.mjs"), await import("../src/canonical.mjs"));
  assert.deepEqual(wrong, []);
});

// The refusals for a string only a newer Unicode composes, for files that are not regular files, and for impossible record times carry exact words, the same as the Python verifier's (the recorded verdicts
// leave every `detail` out, so these are compared here).
const EXACT_DETAILS = {
  "committed by hand: a record string that only a newer Unicode composes": ["integrity.items.item-00000000.detail", "code point U+105D2 is not assigned in Unicode 15.0"],
  "committed by hand: a forecast created a day after its commit": ["timing.items.item-00000001.detail", "created_at is more than 1 s later than committed_at"],
  "committed by hand: a forecast created 1.001 s after its commit": ["timing.items.item-00000001.detail", "created_at is more than 1 s later than committed_at"],
  "a record file one byte over the size limit is refused": ["integrity.items.item-00000001.detail", "record file"],
};

export function detailDeviations(verifyMod, rootMod) {
  const wrong = [];
  for (const [name, [path, text]] of Object.entries(EXACT_DETAILS)) {
    const index = cases.findIndex((c) => c.name === name);
    if (index < 0) {
      wrong.push(`${name}: not recorded`);
      continue;
    }
    const c = cases[index];
    const root = c.root === null ? null : rootMod.parseRoot(Buffer.from(c.root, "base64"));
    const got = path.split(".").reduce((node, part) => (node !== null && typeof node === "object" ? node[part] : undefined), verifyMod.verifyBundle(trees[index], root, c.status));
    if (got !== text) wrong.push(`${name}: ${path} is ${JSON.stringify(got)}`);
  }
  return wrong;
}

test("the new refusals carry the same words as in the Python verifier", async () => {
  assert.deepEqual(detailDeviations(await import("../src/verify.mjs"), await import("../src/trusted_root.mjs")), []);
});

test("every recorded bundle gets the verdict the Python verifier gave", async () => {
  const verifyMod = await import("../src/verify.mjs");
  const rootMod = await import("../src/trusted_root.mjs");
  const wrong = [];
  cases.forEach((c, i) => {
    const got = verdictOf(verifyMod, rootMod, i);
    const expected = scrub(c.python);
    // key order is not part of the verdict; compare as sorted JSON
    const sorted = (v) => JSON.stringify(v, (k, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : 1))) : x));
    if (sorted(got) !== sorted(expected)) wrong.push(c.name);
  });
  assert.deepEqual(wrong, []);
});

test("the cases include scorable bundles, every level and every root state", () => {
  const verdicts = cases.map((c) => c.python);
  assert.ok(verdicts.some((v) => v.eligibility.scorable));
  for (const level of ["C3", "C1", "none"]) assert.ok(verdicts.some((v) => v.timing.level === level), level);
  for (const state of ["PINNED", "EXPIRED", "BUNDLE_ASSERTED"]) assert.ok(verdicts.some((v) => v.root_of_trust.state === state), state);
});
