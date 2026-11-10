import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { CanonicalError, canonicalize } from "../src/canonical.mjs";
import { ASSIGNED, UNICODE_VERSION } from "../src/unicode15.mjs";
import { mutantTree } from "./mutate.mjs";

const file = fileURLToPath(new URL("../../tests/vectors/canonical_record.json", import.meta.url));
const { vectors } = JSON.parse(readFileSync(file, "utf8"));

for (const v of vectors) {
  test(`shared vector ${v.id}`, () => {
    const raw = Buffer.from(v.input_hex, "hex");
    if (v.expect_hex === null) assert.throws(() => canonicalize(raw), CanonicalError);
    else assert.equal(canonicalize(raw).toString("hex"), v.expect_hex);
  });
}

test("a __proto__ key is an ordinary key", () => {
  assert.equal(canonicalize(Buffer.from('{"__proto__":1,"a":2}')).toString(), '{"__proto__":1,"a":2}');
});

// ---- the Unicode 15.0 repertoire (finding D-NFC) ---------------------------------------------------------------------------------
// U+105D2 U+0307 is NFC under Python's Unicode 15.0 tables; this runtime's ICU (Unicode 16.0 and later) composes it to U+105C9.
const EXOTIC = "\u{105D2}\u0307";
const UNASSIGNED = (code) => `code point U+${code.toString(16).toUpperCase().padStart(4, "0")} is not assigned in Unicode 15.0`;
const RUNTIME_TOO_OLD = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version";
const record = (text) => Buffer.from(`{"v":"${text}"}`, "utf8");

// The message of the CanonicalError, "<accepted>", or the name of any other error.
function refusal(mod, bytes) {
  try {
    mod.canonicalize(bytes);
    return "<accepted>";
  } catch (error) {
    return error instanceof mod.CanonicalError ? error.message : `<${error.constructor.name}>`;
  }
}

// The rules of the repertoire and of the runtime check; the number of rules a module breaks.
export function repertoireDeviations(mod) {
  const wrong = [];
  const expect = (label, got, wanted) => {
    if (got !== wanted) wrong.push(`${label}: ${got}, expected ${wanted}`);
  };
  expect("U+105D2 U+0307", refusal(mod, record(EXOTIC)), UNASSIGNED(0x105d2));
  expect("U+105D2 U+0307 spelled as escapes", refusal(mod, Buffer.from('{"v":"\\ud801\\uddd2\\u0307"}')), UNASSIGNED(0x105d2));
  expect("a key of U+105D2 U+0307", refusal(mod, Buffer.from(`{"${EXOTIC}":1}`, "utf8")), UNASSIGNED(0x105d2));
  for (const code of [0x2ffc, 0x1fae9, 0x0378, 0x323b0, 0xfdd0, 0xfffe, 0xffff, 0x10fffe, 0x10ffff]) {
    expect(`U+${code.toString(16)} alone`, refusal(mod, record(String.fromCodePoint(code))), UNASSIGNED(code));
  }
  expect("the first unassigned code point is named", refusal(mod, record("ok\u2ffc\u{1FAE9}")), UNASSIGNED(0x2ffc));
  for (const code of [0x0377, 0x037a, 0xe000, 0x1e030, 0x1e08f, 0x31350, 0x323af, 0x1f600, 0xe01ef, 0x10fffd]) {
    expect(`U+${code.toString(16)} (assigned in 15.0)`, refusal(mod, record((code === 0x1e08f ? "a" : "") + String.fromCodePoint(code))), "<accepted>");
  }
  expect("the repertoire before NFC", refusal(mod, record("e\u0301\u2ffc")), UNASSIGNED(0x2ffc));
  expect("NFC of assigned code points", refusal(mod, record("e\u0301")), "string is not NFC");
  // the runtime check: Node's own version when it states one, the normalizer itself otherwise (a browser)
  expect("Unicode 14.0", String(mod.unicodeAtLeast15({ process: { versions: { unicode: "14.0" } } })), "false");
  for (const version of ["15.0", "15.1", "16.0", "17.0"]) expect(`Unicode ${version}`, String(mod.unicodeAtLeast15({ process: { versions: { unicode: version } } })), "true");
  expect("no stated version, this normalizer", String(mod.unicodeAtLeast15({})), "true");
  expect("no stated version, a normalizer that knows nothing of 15.0", String(mod.unicodeAtLeast15({}, (text) => text)), "false");
  expect("a normalizer that knows nothing of 15.0", String(mod.normalizerKnowsUnicode15((text) => text)), "false");
  expect("this normalizer", String(mod.normalizerKnowsUnicode15()), "true");
  const saved = mod.runtime.unicodeAtLeast15;
  try {
    mod.runtime.unicodeAtLeast15 = false;
    for (const [label, call] of [["parseRecord", () => mod.parseRecord(Buffer.from('{"a":"b"}'))], ["canonicalize", () => mod.canonicalize(Buffer.from('{"a":"b"}'))]]) {
      let got = "<accepted>";
      try {
        call();
      } catch (error) {
        got = error instanceof mod.UnicodeVersionError && !(error instanceof mod.CanonicalError) ? error.message : `<${error.constructor.name}>`;
      }
      expect(`${label} on a runtime older than 15.0`, got, RUNTIME_TOO_OLD);
    }
  } finally {
    mod.runtime.unicodeAtLeast15 = saved;
  }
  return wrong;
}

test("the record profile admits only code points assigned in Unicode 15.0, on a runtime of Unicode 15.0 or later", async () => {
  assert.deepEqual(repertoireDeviations(await import("../src/canonical.mjs")), []);
});

test("the table is well formed and is the Python verifier's table", () => {
  assert.equal(UNICODE_VERSION, "15.0.0");
  assert.equal(ASSIGNED[0][0], 0);
  let count = 0;
  for (const [i, [first, last]] of ASSIGNED.entries()) {
    assert.ok(first <= last && last <= 0x10ffff);
    if (i > 0) assert.ok(ASSIGNED[i - 1][1] < first - 1, `ranges overlap or touch at ${first.toString(16)}`);
    count += last - first + 1;
  }
  assert.equal(count, 288767); // Unicode 15.0: 149,186 characters, 65 controls, 137,468 private-use code points, 2,048 surrogates
  const python = readFileSync(fileURLToPath(new URL("../../src/hoshitori/unicode15.py", import.meta.url)), "utf8");
  assert.ok(python.includes('UNICODE_VERSION: Final = "15.0.0"'));
  const pairs = [...python.matchAll(/0x([0-9A-F]{4,6}), 0x([0-9A-F]{4,6})/g)].map((m) => [parseInt(m[1], 16), parseInt(m[2], 16)]);
  assert.deepEqual(pairs, ASSIGNED.map((range) => [...range]));
});

const MUTATIONS = {
  "no repertoire check": ["if (!assigned(code)) fail(", "if (false) fail("],
  "the check runs on ASCII text only": ["if (!/^[\\x00-\\x7f]*$/.test(text)) {", "if (/^[\\x00-\\x7f]*$/.test(text)) {"],
  "the end of a range is not assigned": ["return found >= 0 && code <= ASSIGNED[found][1];", "return found >= 0 && code < ASSIGNED[found][1];"],
  "a range starts one later": ["if (STARTS[middle] <= code) {", "if (STARTS[middle] < code) {"],
  "the repertoire is this runtime's": ["if (!assigned(code)) fail(", "if (/\\p{Cn}/u.test(ch)) fail("],
  "the repertoire comes after NFC": [
    "  if (!/^[\\x00-\\x7f]*$/.test(text)) {\n    // every ASCII",
    "  if (text.normalize(\"NFC\") !== text) fail(\"string is not NFC\");\n  if (!/^[\\x00-\\x7f]*$/.test(text)) {\n    // every ASCII",
  ],
  "the stated version is not read": ["if (typeof reported === \"string\") return atLeast15(reported);", ""],
  "Unicode 14 is enough": ["Number(version.split(\".\")[0]) >= 15", "Number(version.split(\".\")[0]) >= 14"],
  "the normalizer is not asked": ["return normalizerKnowsUnicode15(nfc);\n}", "return true;\n}"],
  "the feature test expects no composition": ["=== \"\\u1EA1\\u{1E08F}\";", "=== \"a\\u{1E08F}\\u0323\";"],
  "the parser does not check the runtime": ["  requireRuntimeUnicode();\n  const result = parseRecordText", "  const result = parseRecordText"],
  "an old runtime is a bad record": ["export class UnicodeVersionError extends Error {}", "export class UnicodeVersionError extends CanonicalError {}"],
};

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree("src/canonical.mjs", oldText, newText);
    assert.ok(repertoireDeviations(mod).length > 0, "the repertoire checks do not notice this change");
  });
}
