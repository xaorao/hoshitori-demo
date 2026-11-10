import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { NoteError, VerifierKey, verifyCheckpoint, verifyNote } from "../src/note.mjs";

const load = (p) => JSON.parse(readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8"));
const spec = load("../../tests/vectors/signed_note_spec_example.json");
const fixture = load("../../tests/vectors/checkpoint_cases.json");

test("the specification's example note verifies and a tampered copy does not", () => {
  const key = VerifierKey.fromVkey(spec.vkey);
  const { text, verified } = verifyNote(Buffer.from(spec.note, "utf8"), [key]);
  assert.equal(text.toString(), "This is an example message.\n");
  assert.deepEqual(verified, ["example.com/foo"]);
  assert.throws(() => verifyNote(Buffer.from(spec.note.replace("example message", "example massage"), "utf8"), [key]), NoteError);
});

test("verifier keys may contain '+' inside the base64 part", () => {
  const vkey = fixture.vkeys.find((v) => v.split("+").length > 3);
  assert.ok(vkey, "the fixture has a key whose base64 part contains '+'");
  assert.equal(VerifierKey.fromVkey(vkey).name.length > 0, true);
});

test("verdicts, sizes and roots agree with the Python implementation on every fixture case", () => {
  let accepted = 0;
  for (const c of fixture.cases) {
    const keys = c.trusted.map((v) => VerifierKey.fromVkey(v));
    let got;
    try {
      const cp = verifyCheckpoint(Buffer.from(c.note_hex, "hex"), keys, c.origin);
      got = { ok: true, size: cp.size.toString(), root: cp.root.toString("hex") };
    } catch (error) {
      assert.ok(error instanceof NoteError, `${c.label}: unexpected ${error}`);
      got = { ok: false };
    }
    assert.equal(got.ok, c.ok, `${c.label}`);
    if (c.ok) {
      assert.equal(got.size, c.size, c.label);
      assert.equal(got.root, c.root, c.label);
      accepted++;
    }
  }
  assert.ok(accepted > 90);
});
