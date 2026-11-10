import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { mutant } from "./mutate.mjs";

const load = (p) => JSON.parse(readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8"));
const fixture = load("../../tests/vectors/checkpoint_cases.json");
const spec = load("../../tests/vectors/signed_note_spec_example.json");

// Number of fixture cases (and spec checks) on which the module deviates from the recorded verdicts.
function deviations(mod) {
  let bad = 0;
  for (const c of fixture.cases) {
    let got;
    try {
      const keys = c.trusted.map((v) => mod.VerifierKey.fromVkey(v));
      const cp = mod.verifyCheckpoint(Buffer.from(c.note_hex, "hex"), keys, c.origin);
      got = { ok: true, size: cp.size.toString(), root: cp.root.toString("hex") };
    } catch (error) {
      if (!(error instanceof mod.NoteError)) bad++;
      got = { ok: false };
    }
    if (got.ok !== c.ok || (c.ok && (got.size !== c.size || got.root !== c.root))) bad++;
  }
  try {
    mod.verifyNote(Buffer.from(spec.note.replace("example message", "example massage"), "utf8"), [mod.VerifierKey.fromVkey(spec.vkey)]);
    bad++;
  } catch (error) {
    if (!(error instanceof mod.NoteError)) bad++;
  }
  return bad;
}

const MUTATIONS = {
  "an untrusted signature is enough": ['if (verified.length === 0) throw new NoteError("no signature from a trusted key");', ""],
  "a bad known signature is ignored": ["throw new NoteError(`signature of ${item.name} does not verify`);", "continue;"],
  "keys matched by name only": ["const key = trusted.get(`${item.name}\\u0000${item.id.toString(\"hex\")}`);", "const key = [...trusted.values()].find((k) => k.name === item.name);"],
  "non-canonical base64 accepted": ['if (data.toString("base64") !== text) throw new NoteError("non-canonical base64");', ""],
  "duplicate signature accepted": ['if (seen.has(ident)) throw new NoteError("duplicate signature for one key name and key id");', ""],
  "origin not checked": ['if (checkpoint.origin !== origin) throw new NoteError("checkpoint is for another log");', ""],
  "extension lines accepted": ["lines.length !== 3 ||", "lines.length < 3 ||"],
  "leading zeros accepted": ["/^(0|[1-9][0-9]*)$/.test(sizeText)", "/^[0-9]+$/.test(sizeText)"],
  "tree size limit removed": ['if (size > MAX_TREE_SIZE) throw new NoteError("tree size out of range");', ""],
  "root length not checked": ['if (root.length !== HASH_LENGTH) throw new NoteError("the root hash must be exactly 32 bytes");', ""],
};

test("the real module has no deviations", async () => {
  assert.equal(deviations(await import("../src/note.mjs")), 0);
});

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`broken rule is detected: ${name}`, async () => {
    const mod = await mutant("../src/note.mjs", oldText, newText);
    assert.ok(deviations(mod) > 0, `mutant not detected: ${name}`);
  });
}
