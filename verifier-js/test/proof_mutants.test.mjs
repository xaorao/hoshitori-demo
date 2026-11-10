import assert from "node:assert/strict";
import { test } from "node:test";
import { mutantTree } from "./mutate.mjs";
import { deviations } from "./proof.test.mjs";

// Redundant guards without a mutant: the explicit refusal of a missing index line and of a missing blank line (the next step refuses the
// same text: the decimal check, and the empty checkpoint).
const MUTATIONS = {
  "any header is accepted": ["if (lines.length === 0 || lines[0] !== HEADER) throw new ProofError(`the first line is ${JSON.stringify(HEADER)}`);", ""],
  "extra data is not read": ['if (position < lines.length && lines[position].startsWith("extra ")) {', "if (false) {"],
  "leading zeros are allowed": ["if (!/^(0|[1-9][0-9]*)$/.test(text)) throw", "if (!/^[0-9]+$/.test(text)) throw"],
  "the index is not range checked": ["if (value > MAX_TREE_SIZE) throw new ProofError(`${what} out of range`);", ""],
  "path hashes may have any length": ['if (raw.length !== 32) throw new ProofError("a path hash is exactly 32 bytes");', ""],
  "the path is unbounded": ['if (path.length > MAX_PATH) throw new ProofError("inclusion path too long");', ""],
  "an empty checkpoint is accepted": ['if (note.length === 0) throw new ProofError("the checkpoint is missing");', ""],
  "carriage returns are accepted": ['if (text.includes("\\r")) throw new ProofError("lines end in a bare newline");', ""],
  "size is not limited": ['if (data.length > MAX_PROOF_BYTES) throw new ProofError("proof too large");', ""],
  "invalid UTF-8 is accepted": ['new TextDecoder("utf-8", { fatal: true, ignoreBOM: true })', 'new TextDecoder("utf-8", { fatal: false, ignoreBOM: true })'],
};

test("the real module deviates on no recorded case", async () => {
  assert.equal(deviations(await import("../src/tlog_proof.mjs")), 0);
});

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree("src/tlog_proof.mjs", oldText, newText);
    assert.ok(deviations(mod) > 0, "the recorded cases do not notice this change");
  });
}
