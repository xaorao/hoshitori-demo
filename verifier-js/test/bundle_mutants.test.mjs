import assert from "node:assert/strict";
import { test } from "node:test";
import { mutantDir } from "./mutate.mjs";
import { deviations, detailDeviations, runtimeDeviations } from "./bundle.test.mjs";

// [file, old text, new text]. Redundant guards without a mutant: the proof-size check before reading (the parser refuses an oversized proof
// with the same code), the explicit refusal of an index line that is missing (the decimal check refuses the same input), and the
// explicit check for missing envelope members (each member has a type check after it that refuses the same record), and the
// `canonicalOk` term of the item test (a record that is not canonical also fails its inclusion proof, and the first failure is the one shown),
// and either one of the two limits of readBundleFile: the size check on the opened descriptor and the read that stops at limit + 1 bytes
// each refuse the same file with the same code when the other is gone (the bounded read is there for a file that grows after it was
// opened, which no recorded case can be). Files that are not regular files cannot be recorded; their rules are in special_files.test.mjs.
// The later part's redundant guards, without a mutant: the order of the two checkpoints' sizes (equal sizes with different roots have no
// consistency path that verifies, so the same bundle fails either way); the cap on the number of hashes in consistency.json and the
// requirement that a size be a whole number of at least one (a longer or shorter path, a size that is not the checkpoint's, and a number the
// canonical form refuses all end as the same CONSISTENCY_FAILED); the filter on later records that failed their own proof (the later part
// is read only when the whole bundle is intact); and the C3 condition on the derived time (a result below C3 has no attested time, so there
// is nothing to derive from either way). The Python verifier has the same guards; its sweep lists them with these reasons.
const MUTATIONS = {
  // reading items
  "canonical form is not required": ["src/verify.mjs", "item.canonicalOk = canonicalize(item.canonicalBytes).equals(item.canonicalBytes);", "item.canonicalOk = true;"],
  "a non-canonical record is not reported": ["src/verify.mjs", 'if (!item.canonicalOk) fail(item, "CANONICAL", "not in canonical form");', ""],
  "the envelope is not validated": ["src/verify.mjs", "    validateCommittedRecord(item.record);\n", ""],
  "an invalid window is ignored": ["src/verify.mjs", 'if (item.windowMs === null) fail(item, "WINDOW"', 'if (false) fail(item, "WINDOW"'],
  "a missing proof is reported as another failure": ["src/verify.mjs", 'fail(item, "MISSING_PROOF"', 'fail(item, "PROOF"'],
  "an unreadable proof is ignored": ["src/verify.mjs", 'fail(item, "PROOF", error.message);\n    return item;', "return item;"],
  "an unreadable checkpoint is ignored": ["src/verify.mjs", 'fail(item, "CHECKPOINT", error.message);\n    return;\n  }\n  if (authenticated', "return;\n  }\n  if (authenticated"],
  "a record file that cannot be read is ignored": ["src/verify.mjs", 'fail(item, "UNREADABLE", String(error.message));\n    return item;', "return item;"],
  "a record file of any size is read": ["src/verify.mjs", "readBundleFile(itemPath, limits.MAX_ITEM_BYTES)", "readBundleFile(itemPath, Infinity)"],
  "a file of exactly the limit is refused": ["src/verify.mjs", "if (info.size > limit) throw", "if (info.size >= limit) throw"],
  "a token over the limit is called unreadable": ["src/verify.mjs", 'reason: error instanceof FileTooLarge ? "SIZE" : "UNREADABLE"', 'reason: "UNREADABLE"'],
  "publication.json of any size is read": ["src/verify.mjs", "readBundleFile(path, limits.MAX_PUBLICATION_BYTES)", "readBundleFile(path, Infinity)"],
  "the creation time is not read": ["src/verify.mjs", 'item.createdMs = parseRfc3339(String(item.record.get("created_at")));', ""],
  // signatures and eras
  "any era key may sign at any size": ["src/verify.mjs", "if (eraCheck(root, era.key, candidate.size) !== ERA.ACCEPT) {", "if (false) {"],
  "a failed signature is called an era failure": ["src/verify.mjs", 'let failure = "SIGNATURE";', 'let failure = "ERA";'],
  "the signature is believed without checking": ["src/verify.mjs", "candidate = verifyCheckpoint(proof.note, [era.key], root.origin);", "candidate = parseCheckpointText(item.noteText);"],
  "the origin is not compared": ["src/verify.mjs", "verifyCheckpoint(proof.note, [era.key], root.origin)", "verifyCheckpoint(proof.note, [era.key], parseCheckpointText(item.noteText).origin)"],
  "an unsigned bundle counts as signed": ["src/verify.mjs", 'signature: "unauthenticated", inclusion: false', 'signature: "verified", inclusion: false'],
  "inclusion is not checked": ["src/verify.mjs", "item.inclusion = verifyInclusion(leafHash(item.canonicalBytes), safe(proof.index), safe(cp.size), proof.path, cp.root);", "item.inclusion = true;"],
  "inclusion uses the proof's own size": ["src/verify.mjs", "safe(cp.size), proof.path, cp.root)", "safe(proof.index) + 1 + proof.path.length, proof.path, cp.root)"],
  "a key may sign before its era": ["src/trusted_root.mjs", "if (big < BigInt(era.fromSize)) return ERA.BEFORE;", ""],
  "a retired key may sign beyond its closing size": ["src/trusted_root.mjs", "if (era.throughSize !== null && big > BigInt(era.throughSize)) return ERA.BEYOND;", ""],
  "an unknown key has an era": ["src/trusted_root.mjs", "if (sameKey(era.key, key)) {\n      if (big", "if (true) {\n      if (big"],
  // the bundle as a whole
  "one checkpoint is not required": ["src/verify.mjs", 'if (texts.size === 1 && whole) return result(items, [], [...texts.values()][0], items[0].checkpoint, null, "none", []);', 'if (true) return result(items, [], [...texts.values()][0] ?? null, items[0]?.checkpoint ?? null, null, "none", []);'],
  "differing checkpoints are not reported": ["src/verify.mjs", 'if (texts.size > 2 || !whole) return result(items, [], null, null, null, "none", ["CHECKPOINTS_DIFFER"]);', 'if (texts.size > 2 || !whole) return result(items, [], null, null, null, "none", []);'],
  "a bad item name is accepted": ["src/verify.mjs", 'else errors.push("BAD_STEM");', "else stems.push(stem);"],
  "an empty bundle is fine": ["src/verify.mjs", 'if (stems.length === 0) errors.push("NO_ITEMS");', ""],
  "a missing items directory is fine": ["src/verify.mjs", 'if (!isDirectory) return { stems: [], errors: ["NO_ITEMS"] };', "if (!isDirectory) return { stems: [], errors: [] };"],
  "too many items are all read": ["src/verify.mjs", "if (stems.length > limits.MAX_ITEMS) return", "if (false) return"],
  "too many items are not reported": ["src/verify.mjs", '{ stems: stems.slice(0, limits.MAX_ITEMS), errors: [...errors, "TOO_MANY_ITEMS"] }', "{ stems: stems.slice(0, limits.MAX_ITEMS), errors }"],
  "structure errors do not matter": ["src/verify.mjs", "&& raw.mismatched.length === 0 && structural.length === 0;", "&& raw.mismatched.length === 0;"],
  "reference mismatches do not matter": ["src/verify.mjs", "refs.mismatched.length === 0 && raw.mismatched.length === 0", "raw.mismatched.length === 0"],
  "raw mismatches do not matter": ["src/verify.mjs", "&& raw.mismatched.length === 0 && structural", "&& structural"],
  "integrity ok does not need a trusted root": ["src/verify.mjs", "ok: consistent && authenticated,", "ok: consistent,"],
  // root states
  "an expired root is treated as asserted": ["src/verify.mjs", 'else if (rootStatus === "EXPIRED") state = "EXPIRED";', 'else if (false) state = "EXPIRED";'],
  "a root whose update failed is still trusted": ["src/verify.mjs", 'state = "BUNDLE_ASSERTED";\n    root = null;', 'state = "BUNDLE_ASSERTED";'],
  "an expired root is called current": ["src/verify.mjs", 'else if (rootStatus === "EXPIRED") state = "EXPIRED";', 'else if (rootStatus === "EXPIRED") state = "PINNED";'],
  // references and raw payloads
  "reference hashes are not compared": ["src/verify.mjs", "else if (byId.get(rid).has(digest)) resolved += 1;", "else if (true) resolved += 1;"],
  "unresolved references are not listed": ["src/verify.mjs", "if (!byId.has(rid)) unresolved.push(label);\n      else if", "if (!byId.has(rid)) {}\n      else if"],
  "resolved references are not counted": ["src/verify.mjs", "has(digest)) resolved += 1;", "has(digest)) {}"],
  "raw payloads are not hashed": ["src/verify.mjs", "info.isFile() && info.size <= limits.MAX_RAW_BYTES && sha256Hex(readFileSync(path)) === digest", "info.isFile() && info.size <= limits.MAX_RAW_BYTES"],
  "a directory counts as a raw payload": ["src/verify.mjs", "info.isFile() && info.size", "info.size"],
  "a raw payload may be larger than the limit": ["src/verify.mjs", "info.size <= limits.MAX_RAW_BYTES &&", "true &&"],
  "missing raw payloads are not listed": ["src/verify.mjs", "      missing.push(digest);\n      continue;", "      continue;"],
  "stray raw files are not checked": ["src/verify.mjs", "if (!cited.has(name) && !intact(join(rawDir, name), name)) mismatched.push(name);", ""],
  "unreferenced raw files are not listed": ["src/verify.mjs", "unreferenced: names.filter((n) => !cited.has(n)).sort(bySortedName)", "unreferenced: []"],
  // the cycle's whole batch
  "an incomplete batch can score": ["src/verify.mjs", "if (!verdict.cycle.complete) reasons.push(", "if (false) reasons.push("],
  "the named root is not compared": ["src/verify.mjs", 'out.complete = new MerkleTree(leaves).root().toString("hex") === named;', "out.complete = true;"],
  "a missing member of the batch is tolerated": ["src/verify.mjs", "if (out.members_present !== count) return out;", ""],
  "a batch size of zero or below is read": ["src/verify.mjs", "out.batch_size === null || out.batch_size < 1 ||", "out.batch_size === null ||"],
  "a boolean is read as a batch size": ["src/verify.mjs", 'typeof size === "number" && Number.isInteger(size) ? size : null', "Number.isInteger(size) || size === true ? Number(size) : null"],
  "a committing step that failed its proof is trusted": ["src/verify.mjs", "if (step.error !== null || !step.canonicalOk || !step.inclusion || step.proof === null ||", "if (step.proof === null ||"],
  "members that failed their proof are counted": ["src/verify.mjs", "if (item.proof !== null && item.error === null && item.canonicalOk && item.inclusion) held.set", "if (item.proof !== null) held.set"],
  "more than one committing step is accepted": ["src/verify.mjs", "if (steps.length !== 1 || !checkpointsOk) return out;", "if (steps.length === 0 || !checkpointsOk) return out;"],
  "a bundle with no forecast can score": ["src/verify.mjs", "if (forecasts === 0) reasons.push(", "if (false) reasons.push("],
  "any record counts as a forecast": ["src/verify.mjs", 'i.record !== null && i.record.get("type") === FORECAST_TYPE && i.error === null && i.inclusion', "i.record !== null && i.error === null && i.inclusion"],
  "a forecast that failed its proof is counted": ["src/verify.mjs", 'i.record.get("type") === FORECAST_TYPE && i.error === null && i.inclusion', 'i.record.get("type") === FORECAST_TYPE'],
  // the later part: a second checkpoint and what the records after it show
  // the settlement of a forecast in the later part
  "a settlement a step cites and the bundle lacks is not refused": ["src/verify.mjs", "if (typeof cited === \"string\") shown.settlement = \"absent\";", "if (false) shown.settlement = \"absent\";"],
  "two settlements of one call are not refused": ["src/verify.mjs", "if (records.length > 1) {\n    shown.settlement = \"conflicting\";", "if (false) {\n    shown.settlement = \"conflicting\";"],
  "a settlement with another set of members is not refused": ["src/verify.mjs", "    body.size !== SETTLEMENT_MEMBERS.length ||\n    !SETTLEMENT_MEMBERS.every((k) => body.has(k)) ||\n", ""],
  "a state a call does not end in is not refused": ["src/verify.mjs", "    !ENDING_STATES.includes(body.get(\"state\")) ||\n", ""],
  "the types of the members are not read": ["src/verify.mjs", "    ![\"outcome\", \"reason\", \"p0\", \"outcome_price\"].every(textOrNull) ||\n", ""],
  "the contract member is not read": ["src/verify.mjs", "    typeof body.get(\"contract\") !== \"string\"\n", "    false\n"],
  "a reference to another record than the forecast is not refused": ["src/verify.mjs", "refs[0].get(\"id\") === forecastId);", "true);"],
  "the id a step cites is not compared with the record's": ["src/verify.mjs", "wrong = wrong || cited !== record.get(\"id\");", "wrong = wrong;"],
  "the outcome is not held to the prices": ["src/verify.mjs", " || body.get(\"outcome\") !== (order > 0 ? \"1\" : \"0\")", ""],
  "a tie that is settled is accepted": ["src/verify.mjs", "wrong = wrong || order === 0 || body", "wrong = wrong || body"],
  "a void for a tie whose prices differ is accepted": ["src/verify.mjs", "wrong = wrong || comparePrices(last, first) !== 0;", "wrong = wrong;"],
  "a missing first or last price is not refused": ["src/verify.mjs", "if (first === null || last === null) {\n      wrong = true;", "if (first === null || last === null) {\n      wrong = wrong;"],
  "a price of zero is read as a price": ["src/verify.mjs", "return /[1-9]/.test(value) ? value : null;", "return value;"],
  "a contract other than the forecast's is accepted": ["src/verify.mjs", "} else if (contract !== null && body.get(\"contract\") !== contract) {", "} else if (false) {"],
  "the end of the call's window is not held": ["src/verify.mjs", "late = item.committedMs < originAt + horizon;", "late = false;"],
  "a faulted settlement does not fail the call": ["src/verify.mjs", "\"malformed\"].includes(origin) || faulted)", "\"malformed\"].includes(origin))"],
  "a settlement written early is not a fault": ["src/verify.mjs", "const SETTLEMENT_FAULTS = [\"absent\", \"conflicting\", \"malformed\", \"mismatch\", \"early\"];", "const SETTLEMENT_FAULTS = [\"absent\", \"conflicting\", \"malformed\", \"mismatch\"];"],
  "a missing consistency file is tolerated": ["src/verify.mjs", 'if (file.state === "missing") return result(stamped, later, smallText, small, large, "missing", ["CHECKPOINTS_DIFFER"]);', ""],
  "a consistency proof that fails is believed": ["src/verify.mjs", "verifyConsistency(safeSize(small.size), safeSize(large.size), hashes, small.root, large.root);", "true;"],
  "the sizes in the consistency file are not compared": ["src/verify.mjs", "BigInt(file.document.get(\"from_size\")) === small.size &&\n      BigInt(file.document.get(\"to_size\")) === large.size &&", ""],
  "a consistency file that is not canonical is read": ["src/verify.mjs", "if (!canonicalize(raw).equals(raw)) return { state: \"failed\", document: null };", ""],
  "a consistency file with another member is read": ["src/verify.mjs", "keys.length !== members.length ||", ""],
  "a consistency file of another format is read": ["src/verify.mjs", "document.get(\"format\") !== CONSISTENCY_FORMAT ||", ""],
  "a path hash may be any text": ["src/verify.mjs", "!hashes.every((h) => typeof h === \"string\" && HASH.test(h)) ||", ""],
  "a failed consistency is reported as differing checkpoints": ["src/verify.mjs", 'return result(stamped, later, smallText, small, large, "failed", ["CONSISTENCY_FAILED"]);', 'return result(stamped, later, smallText, small, large, "failed", ["CHECKPOINTS_DIFFER"]);'],
  "later records are judged by the token window": ["src/verify.mjs", "verdict.timing = timingPart(directory, groups.stamped,", "verdict.timing = timingPart(directory, items,"],
  "later records are not listed": ["src/verify.mjs", "verdict.timing.later_items = groups.later.map((i) => i.stem);", "verdict.timing.later_items = [];"],
  "the later part is shown when the bundle is not intact": ["src/verify.mjs", 'if (groups.later.length === 0 || groups.consistency !== "verified" || !integrityOk) return out;', "if (groups.later.length === 0) return out;"],
  "an eligible time before the derived one passes": ["src/verify.mjs", 'awaiting = eligible >= derived ? "ok" : "early";', 'awaiting = "ok";'],
  "an eligible time at the derived one fails": ["src/verify.mjs", 'awaiting = eligible >= derived ? "ok" : "early";', 'awaiting = eligible > derived ? "ok" : "early";'],
  "an origin before the derived one passes": ["src/verify.mjs", 'if (originAt < (Math.floor(derived / MINUTE_MS) + 1) * MINUTE_MS) origin = "early";', "if (false) origin = \"early\";"],
  "an origin that is not the next close passes": ["src/verify.mjs", '|| originAt !== (Math.floor(eligible / MINUTE_MS) + 1) * MINUTE_MS) origin = "wrong";', ") origin = \"wrong\";"],
  "an origin after an eligible time that is not ok passes": ["src/verify.mjs", 'eligible === null || awaiting !== "ok" ||', "eligible === null ||"],
  "a second waiting step is ignored": ["src/verify.mjs", 'waiting.length > 1 ? "conflicting"', 'waiting.length > 1 ? "ok"'],
  "a second opening step is ignored": ["src/verify.mjs", 'opened.length > 1 ? "conflicting"', 'opened.length > 1 ? "ok"'],
  "a missing waiting step passes": ["src/verify.mjs", 'let awaiting = waiting.length === 0 ? "absent"', 'let awaiting = waiting.length === 0 ? "ok"'],
  "a malformed step time passes": ["src/verify.mjs", 'eligible === null ? "malformed" : "unchecked";', 'eligible === null ? "ok" : "unchecked";'],
  "origin failures do not make a bundle unscorable": ["src/verify.mjs", 'if (verdict.cycle.later.failures > 0) reasons.push("ORIGIN_BEFORE_ATTESTATION");', ""],
  "a committed level is taken from two steps": ["src/verify.mjs", 'const level = committed.length === 1 && typeof', 'const level = committed.length >= 1 && typeof'],
  "an ending is taken from two steps": ["src/verify.mjs", 'const ending = ended.length > 1 ? "conflicting"', 'const ending = ended.length > 1 ? ended[0].get("state")'],
  "the stamped checkpoint is not the smaller one": ["src/verify.mjs", "(a.size < b.size ? -1 : a.size > b.size ? 1 : Buffer.compare(a.text, b.text))", "(a.size < b.size ? 1 : a.size > b.size ? -1 : Buffer.compare(a.text, b.text))"],
  // timing
  "the tokens stamp something other than the checkpoint": ["src/verify.mjs", 'createHash("sha256").update(stampedText).digest()', 'createHash("sha256").update(Buffer.alloc(0)).digest()'],
  "no root is pinned for tokens": ["src/verify.mjs", "const policy = new TokenPolicy(rules.pinned);", "const policy = new TokenPolicy([]);"],
  "unauthenticated bundles get a level": ["src/verify.mjs", "if (!authenticated || root === null) {", "if (false) {"],
  "broken bundles get a level": ["src/verify.mjs", "if (!integrityOk) {", "if (false) {"],
  "every record is a subject": ["src/verify.mjs", "const subjects = withWindows.length ? withWindows : [...items];", "const subjects = [...items];"],
  "one subject at C3 is enough": ["src/verify.mjs", "levels.every((l) => l === timing.LEVEL_C3)", "levels.some((l) => l === timing.LEVEL_C3)"],
  "too many tokens are all read": ["src/verify.mjs", "if (index >= limits.MAX_TOKENS) {", "if (false) {"],
  "token read errors are not reported": ["src/verify.mjs", "evidence.push({ name: label, verified: null, reason: error.code });", ""],
  "public claims raise the level": ["src/verify.mjs", "authenticated_offline: 0, note", "authenticated_offline: claims, note"],
  "unreadable publication files are ignored": ["src/verify.mjs", "      claims = -1;\n    }\n  }\n  return { claimed", "      claims = 0;\n    }\n  }\n  return { claimed"],
  "a publication whose claims are not a list is trusted": ["src/verify.mjs", "claims = Array.isArray(list) ? list.length : -1;", "claims = Array.isArray(list) ? list.length : 0;"],
  // eligibility
  "an expired root can score": ["src/verify.mjs", 'if (state !== "PINNED") reasons.push("ROOT_NOT_CURRENT");', ""],
  "broken integrity can score": ["src/verify.mjs", 'if (!verdict.integrity.ok) reasons.push("INTEGRITY");', ""],
  "a low level can score": ["src/verify.mjs", 'if (verdict.timing.level !== timing.LEVEL_C3) reasons.push("LEVEL_BELOW_C3");', ""],
  "any data mode can score": ["src/verify.mjs", 'if (modes.size !== 1 || !modes.has(required)) reasons.push("DATA_MODE");', ""],
  "several data modes can score": ["src/verify.mjs", "if (modes.size !== 1 || !modes.has(required))", "if (!modes.has(required))"],
  "no evidence can score": ["src/verify.mjs", "if (modes.size !== 1 || !modes.has(required))", "if (modes.size > 1 || (modes.size === 1 && !modes.has(required)))"],
  "a missing data mode is ignored": ["src/verify.mjs", 'modes.add(typeof mode === "string" ? mode : "missing");', 'if (typeof mode === "string") modes.add(mode);'],
  // (the time-stamp counting rules are tested by timing_mutants.test.mjs on recorded assessments)
  // the record envelope
  "unknown members are tolerated": ["src/envelope.mjs", 'if (unknown.length) throw new EnvelopeError(`unknown envelope members: ${unknown.sort()}`);', ""],
  "the spec is not checked": ["src/envelope.mjs", "if (record.get(\"spec\") !== SPEC) throw new EnvelopeError(`spec must be ${SPEC}`);", ""],
  "the type is not checked": ["src/envelope.mjs", 'if (typeof kind !== "string" || !TYPE.test(kind)) throw', "if (false) throw"],
  "the type version is not checked": ["src/envelope.mjs", 'if (typeof version !== "number" || !Number.isInteger(version) || version < 1) throw', "if (false) throw"],
  "a boolean passes as the type version": ["src/envelope.mjs", 'if (typeof version !== "number" || !Number.isInteger(version) || version < 1) throw', "if (version < 1) throw"],
  "the id is not checked": ["src/envelope.mjs", 'if (typeof id !== "string" || !ID.test(id)) throw', "if (false) throw"],
  "the source is not checked": ["src/envelope.mjs", 'if (typeof source !== "string" || !source.startsWith("urn:") || [...source].length > 256 || !/^[\\x00-\\x7f]*$/.test(source)) throw', "if (false) throw"],
  "a long source is accepted": ["src/envelope.mjs", "|| [...source].length > 256 ||", "||"],
  "the body is not checked": ["src/envelope.mjs", 'if (!(record.get("body") instanceof Map)) throw new EnvelopeError("body must be an object");', ""],
  "a ref may carry extra members": ["src/envelope.mjs", "ref instanceof Map && ref.size === 2 &&", "ref instanceof Map &&"],
  "a ref hash may be any text": ["src/envelope.mjs", "&& SHA256_HEX.test(ref.get(\"sha256\"));", ";"],
  "the privacy value is not checked": ["src/envelope.mjs", "if (!PRIVACY.has(privacy)) throw", "if (false) throw"],
  // the standing list of what is not proven (VERIFICATION_SPEC section 1, item 4 and section 2)
  "the list leaves out the authorities": ["src/verify.mjs", '  "both authorities honest at once: the level assumes that the time-stamp authorities did not collude or share a time source",\n', ""],
  "a bundle without a root does not say where it may come from": ["src/verify.mjs", 'state === "BUNDLE_ASSERTED" ? [...NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] : [...NOT_PROVEN]', "[...NOT_PROVEN]"],
  "every bundle says it may not come from Hoshitori": ["src/verify.mjs", 'state === "BUNDLE_ASSERTED" ? [...NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] : [...NOT_PROVEN]', "[...NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT]"],
};

// On a runtime older than Unicode 15.0 (bundle.test.mjs `runtimeDeviations`). Without a mutant: the rethrow of UnicodeVersionError when a
// record is parsed in loadItem, which verifyBundle's own check, made first, never lets the error reach.
const RUNTIME_MUTATIONS = {
  "the verifier does not check the runtime": ["src/verify.mjs", "  requireRuntimeUnicode(); // no verdict on a runtime that cannot check the canonical form\n", ""],
  "an old runtime makes a malformed root": ["src/trusted_root.mjs", "if (error instanceof RootError || error instanceof UnicodeVersionError) throw error;", "if (error instanceof RootError) throw error;"],
};

// The exact words of the new refusals (bundle.test.mjs `detailDeviations`).
const DETAIL_MUTATIONS = {
  "the order of a record's times is not described": ["src/verify.mjs", "detail: result.error === null ? null : timing.orderDetail(rules),", "detail: null,"],
  "the order is described with another limit": ["src/timing.mjs", "${rules.windowBeforeCommitS} s later", "${rules.windowAfterCommitS} s later"],
  "a record file over the limit is described otherwise": ["src/verify.mjs", 'if (error instanceof FileTooLarge) fail(item, "TOO_LARGE", "record file");', 'if (error instanceof FileTooLarge) fail(item, "TOO_LARGE", error.message);'],
};

test("the real modules deviate on no recorded case", async () => {
  const load = mutantDir("src/verify.mjs", 'export const EVIDENCE_TYPE = "evidence";', 'export const EVIDENCE_TYPE = "evidence";');
  assert.equal(deviations(await load("verify.mjs"), await load("trusted_root.mjs")), 0);
});

for (const [name, [file, oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const load = mutantDir(file, oldText, newText);
    assert.ok(deviations(await load("verify.mjs"), await load("trusted_root.mjs")) > 0, "the recorded cases do not notice this change");
  });
}

for (const [name, [file, oldText, newText]] of Object.entries(RUNTIME_MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const load = mutantDir(file, oldText, newText);
    assert.ok(runtimeDeviations(await load("verify.mjs"), await load("trusted_root.mjs"), await load("canonical.mjs")).length > 0, "the runtime check does not notice this change");
  });
}

for (const [name, [file, oldText, newText]] of Object.entries(DETAIL_MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const load = mutantDir(file, oldText, newText);
    assert.ok(detailDeviations(await load("verify.mjs"), await load("trusted_root.mjs")).length > 0, "the exact words are not compared");
  });
}
