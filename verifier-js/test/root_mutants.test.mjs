import assert from "node:assert/strict";
import { test } from "node:test";
import { mutantTree } from "./mutate.mjs";
import { deviations } from "./root.test.mjs";

// [old text, new text]; each target occurs exactly once in src/trusted_root.mjs. Redundant guards without a mutant: the base64 alphabet
// pattern (the re-encoding comparison refuses the same text), the document size limit (a canonical document that large breaks a field limit
// first). The era check is tested through bundles (bundle_mutants.test.mjs).
const MUTATIONS = {
  "a non-canonical document is accepted": ["if (!canonicalize(payload).equals(payload)) throw fail(\"document is not in canonical form\");", ""],
  "unknown or missing members are tolerated": ["if (missing.length || extra.length) throw fail(", "if (false) throw fail("],
  "the document type is not checked": ["if (top.get(\"type\") !== TYPE) throw fail(\"not a trusted-root document\");", ""],
  "the format version is not checked": ["if (top.get(\"format_version\") !== FORMAT_VERSION || typeof top.get(\"format_version\") !== \"number\") throw fail(`format_version must be ${FORMAT_VERSION}`);", ""],
  "a boolean passes as an integer": ["if (typeof value !== \"number\" || !Number.isInteger(value) || value < minimum || value > maximum) throw", "if (value < minimum || value > maximum) throw"],
  "integers are not range checked": ["|| value < minimum || value > maximum) throw", ") throw"],
  "a non-string passes as text": ["if (typeof value !== \"string\" || value.length === 0 || codePoints(value) > limit) throw", "if (false) throw"],
  "text length counts code units, not characters": ["codePoints(value) > limit", "value.length > limit"],
  "hex text is not checked": ["if (typeof value !== \"string\" || value.length !== length || !/^[0-9a-f]*$/.test(value)) throw", "if (false) throw"],
  "lists are not bounded": ["if (!Array.isArray(value) || value.length < minimum || value.length > MAX_LIST) throw", "if (!Array.isArray(value)) throw"],
  "base64 need not be canonical": ["if (raw.toString(\"base64\") !== value || (length !== null && raw.length !== length)) throw", "if (length !== null && raw.length !== length) throw"],
  "base64 length is not checked": ["if (raw.toString(\"base64\") !== value || (length !== null && raw.length !== length)) throw", "if (raw.toString(\"base64\") !== value) throw"],
  "a root key may be listed twice": ["if (rootKeys.has(identifier)) throw fail(\"a root key is listed twice\");", ""],
  "a threshold of zero is allowed": ["integer(top.get(\"root_threshold\"), \"root_threshold\", 1, rootKeys.size)", "integer(top.get(\"root_threshold\"), \"root_threshold\", 0, rootKeys.size)"],
  "a threshold above the key count is allowed": ["integer(top.get(\"root_threshold\"), \"root_threshold\", 1, rootKeys.size)", "integer(top.get(\"root_threshold\"), \"root_threshold\", 1)"],
  "an era key may be named for another log": ["if (key.name !== origin) throw fail(", "if (false) throw fail("],
  "the open era may be anywhere": ["if (open.length !== 1 || eras[eras.length - 1].throughSize !== null) throw", "if (false) throw"],
  "several open eras are allowed": ["if (open.length !== 1 || eras[eras.length - 1].throughSize !== null) throw", "if (eras[eras.length - 1].throughSize !== null) throw"],
  "a log key may appear twice": ["if (sameKey(eras[i].key, eras[j].key)) throw fail(\"a log key appears in two eras\");", ";"],
  "an era may close before it opens": ["if (era.throughSize !== null && era.throughSize < era.fromSize) throw", "if (false) throw"],
  "eras may overlap": ["if (era.fromSize <= previousThrough) throw", "if (false) throw"],
  "eras may touch": ["if (era.fromSize <= previousThrough) throw", "if (era.fromSize < previousThrough) throw"],
  "a pin may name an unknown operator": ["if (!operators.has(name)) throw", "if (false) throw"],
  "a root may be pinned twice": ["if (new Set(pinned.map((p) => p.sha256)).size !== pinned.length) throw", "if (false) throw"],
  "the close may precede the window": ["integer(stamp.get(\"close_after_commit_s\"), \"close_after_commit_s\", windowAfter, 7200)", "integer(stamp.get(\"close_after_commit_s\"), \"close_after_commit_s\", 0, 7200)"],
  "no independent operator may be required": ["integer(stamp.get(\"required_independent\"), \"required_independent\", 2, 16)", "integer(stamp.get(\"required_independent\"), \"required_independent\", 0, 16)"],
  "one operator may be enough": ["integer(stamp.get(\"required_independent\"), \"required_independent\", 2, 16)", "integer(stamp.get(\"required_independent\"), \"required_independent\", 1, 16)"],
  "no clock uncertainty may be assumed": ["integer(stamp.get(\"u_constant_s\"), \"u_constant_s\", 1, 3600)", "integer(stamp.get(\"u_constant_s\"), \"u_constant_s\", 0, 3600)"],
  "witnesses required may exceed the keys": ["integer(witnesses.get(\"required\"), \"witnesses required\", 0, witnessKeys.length)", "integer(witnesses.get(\"required\"), \"witnesses required\", 0)"],
  "an envelope may carry extra members": ["Object.keys(value).sort().join() !== \"payload_b64,signatures\"", "false"],
  "signatures may be unbounded": ["if (!Array.isArray(raw) || raw.length > MAX_SIGNATURES) throw", "if (!Array.isArray(raw)) throw"],
  "a signature may carry extra members": ["Object.keys(item).sort().join() !== \"keyid,sig_b64\"", "false"],
  "signatures are counted instead of keys": ["return counted.size;", "return signatures.filter(([id]) => root.rootKeys.has(id)).length;"],
  "signatures are not verified": ["if (verify(null, payload, key, signature)) counted.add(identifier);", "counted.add(identifier);"],
  "unknown keys count": ["if (publicKey === undefined) continue;", "if (false) continue;"],
  "the old threshold is not required": ["if (countValid(current, signatures, payload) < current.rootThreshold) return result(\"REJECTED\", CODES.OLD_THRESHOLD);", ""],
  "the new threshold is not required": ["if (countValid(proposed, signatures, payload) < proposed.rootThreshold) return result(\"REJECTED\", CODES.NEW_THRESHOLD);", ""],
  "the version step is not checked": ["if (proposed.version !== wanted) return result(\"REJECTED\", CODES.VERSION_STEP);", ""],
  "the chain length is not bounded": ["if (steps >= maxChain) return result(\"REJECTED\", CODES.CHAIN_BOUND);", ""],
  "the chain bound is off by one": ["if (steps >= maxChain) return", "if (steps > maxChain) return"],
  "different documents are not a fork": ["if (marks.size > 1) return result(\"FORK\", CODES.FORK);", ""],
  "only the first channel's signatures count": ["const signatures = parsed.flatMap((p) => p.signatures);", "const signatures = parsed[0].signatures;"],
  "a malformed envelope is skipped": ["if (error instanceof RootError) return result(\"REJECTED\", CODES.MALFORMED);\n      throw error;\n    }\n    const payload", "if (error instanceof RootError) return result(\"CURRENT\", CODES.OK);\n      throw error;\n    }\n    const payload"],
  "an unparsable document is skipped": ["if (error instanceof RootError) return result(\"REJECTED\", CODES.MALFORMED);\n      throw error;\n    }\n    if (countValid(current", "if (error instanceof RootError) return result(\"CURRENT\", CODES.OK);\n      throw error;\n    }\n    if (countValid(current"],
  "expiry is not checked": ["if (current.expires <= nowMs) return result(\"EXPIRED\", CODES.EXPIRED);", ""],
  "expiry at the start time is allowed": ["if (current.expires <= nowMs) return", "if (current.expires < nowMs) return"],
  "rollback is not checked": ["if (current.version < floorVersion) return result(\"REJECTED\", CODES.ROLLBACK);", ""],
  "the single-channel flag is never set": ["      single = true; // a step needs", "      single = false; // a step needs"],
  "a step from one channel is followed": ["    if (candidates.length < 2) {", "    if (candidates.length < 1) {"],
  "the flag is set for a step two channels serve": ["    const payload = parsed[0].payload;", "    single = true;\n    const payload = parsed[0].payload;"],
};

test("the real module deviates on no recorded case", async () => {
  assert.equal(deviations(await import("../src/trusted_root.mjs")), 0);
});

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree("src/trusted_root.mjs", oldText, newText);
    assert.ok(deviations(mod) > 0, "the recorded cases do not notice this change");
  });
}
