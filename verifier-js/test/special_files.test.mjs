// Bundle files that are not regular files, or far larger than any valid file. A FIFO or a link to a device reports
// size 0 to stat, and a FIFO that no process writes blocks an ordinary open for ever, so each must be refused from its opened
// descriptor without being read. The recorded bundle cases cannot hold a FIFO, so these trees are built here, from the recorded honest
// bundle, and every verdict is taken in a child process: a verifier that blocks is stopped by the timeout and counts as wrong.
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { closeSync, cpSync, ftruncateSync, mkdirSync, mkdtempSync, openSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { mutantSourceDir } from "./mutate.mjs";
import { loadCases } from "./vectors.mjs";

const honest = loadCases("bundle_cases").find((c) => c.name === "honest");
const FC = "item-00000001";
const SOURCE = fileURLToPath(new URL("../src/", import.meta.url));
const MiB = 1024 * 1024;

const honestTree = (() => {
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-special-"));
  for (const d of honest.directories) mkdirSync(join(dir, d), { recursive: true });
  for (const [path, content] of Object.entries(honest.files)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), Buffer.from(content, "base64"));
  }
  return dir;
})();

const refusedRecord = { "integrity.ok": false, [`integrity.items.${FC}.error`]: "UNREADABLE", [`integrity.items.${FC}.detail`]: "not a regular file" };
const refusedProof = { "integrity.ok": false, [`integrity.items.${FC}.error`]: "MISSING_PROOF", [`integrity.items.${FC}.detail`]: `${FC}.tlog-proof` };
const thirdUnreadable = { "integrity.ok": true, "timing.level": "C3", "timing.tokens.2.name": "c", "timing.tokens.2.reason": "UNREADABLE" };
const record = `items/${FC}.canonical.json`;
const proof = `items/${FC}.tlog-proof`;
const token = "tsa/c.tsr";
const publication = "publication.json";

const SCENARIOS = [
  ["a record file that is a FIFO no process writes", record, "fifo", refusedRecord],
  ["a proof file that is a FIFO no process writes", proof, "fifo", refusedProof],
  ["a token that is a FIFO no process writes", token, "fifo", thirdUnreadable],
  ["publication.json that is a FIFO no process writes", publication, "fifo", { "integrity.ok": true, "timing.level": "C3", "timing.public_commit.claimed": -1 }],
  ["a record file that links to the null device", record, "null", refusedRecord],
  ["a proof file that links to the null device", proof, "null", refusedProof],
  ["a token that links to the null device", token, "null", thirdUnreadable],
  ["publication.json that links to the null device", publication, "null", { "integrity.ok": true, "timing.public_commit.claimed": -1 }],
  ["a 64 MiB record file", record, "sparse", { "integrity.ok": false, [`integrity.items.${FC}.error`]: "TOO_LARGE", [`integrity.items.${FC}.detail`]: "record file" }],
  ["a 64 MiB proof file", proof, "sparse", { "integrity.ok": false, [`integrity.items.${FC}.error`]: "PROOF", [`integrity.items.${FC}.detail`]: "proof file too large" }],
  ["a 64 MiB token", token, "sparse", { "integrity.ok": true, "timing.level": "C3", "timing.tokens.2.reason": "SIZE" }],
  ["a 64 MiB publication.json", publication, "sparse", { "integrity.ok": true, "timing.public_commit.claimed": -1 }],
];

const trees = SCENARIOS.map(([, where, kind]) => {
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-special-"));
  cpSync(honestTree, dir, { recursive: true });
  const target = join(dir, where);
  try {
    unlinkSync(target);
  } catch {
    // the third token is new
  }
  if (kind === "fifo") execFileSync("mkfifo", [target]);
  else if (kind === "null") symlinkSync("/dev/null", target);
  else {
    const fd = openSync(target, "w");
    ftruncateSync(fd, 64 * MiB); // a sparse regular file: nothing of it may be read
    closeSync(fd);
  }
  return dir;
});

const lookup = (verdict, path) => path.split(".").reduce((node, part) => (node !== null && typeof node === "object" && part in node ? node[part] : "<absent>"), verdict);

// The verdicts of the verifier in `srcDir` on every scenario tree, taken in one child process, each with how much the resident memory
// grew while it was taken; null when the child did not finish within the timeout (it blocked) or failed.
function verdictsIn(srcDir) {
  const url = (name) => JSON.stringify(pathToFileURL(join(srcDir, name)).href);
  const script = [
    `const { verifyBundle } = await import(${url("verify.mjs")});`,
    `const { parseRoot } = await import(${url("trusted_root.mjs")});`,
    `const root = parseRoot(Buffer.from(${JSON.stringify(honest.root)}, "base64"));`,
    "const out = [];",
    `for (const dir of ${JSON.stringify(trees)}) {`,
    "  const before = process.resourceUsage().maxRSS;",
    '  const verdict = JSON.parse(JSON.stringify(verifyBundle(dir, root, "CURRENT")));',
    "  out.push({ verdict, grewBytes: (process.resourceUsage().maxRSS - before) * 1024 });",
    "}",
    "process.stdout.write(JSON.stringify(out));",
  ].join("\n");
  const done = spawnSync(process.execPath, ["--input-type=module", "-e", script], { timeout: 20000, encoding: "utf8", maxBuffer: 1 << 26 });
  if (done.error || done.status !== 0) return null;
  return JSON.parse(done.stdout);
}

// The scenarios on which the verifier in `srcDir` is wrong.
export function specialDeviations(srcDir) {
  const answers = verdictsIn(srcDir);
  if (answers === null) return ["no verdicts: the verifier blocked or failed"];
  const wrong = [];
  SCENARIOS.forEach(([name, , kind, expect], index) => {
    const got = answers[index];
    for (const [path, wanted] of Object.entries(expect)) {
      if (JSON.stringify(lookup(got.verdict, path)) !== JSON.stringify(wanted)) wrong.push(`${name}: ${path} is ${JSON.stringify(lookup(got.verdict, path))}, expected ${JSON.stringify(wanted)}`);
    }
    if (kind === "sparse" && got.grewBytes > 32 * MiB) wrong.push(`${name}: memory grew by ${got.grewBytes} bytes`);
  });
  return wrong;
}

test("files that are not regular files, or far too large, are refused without being read", () => {
  assert.deepEqual(specialDeviations(SOURCE), []);
});

// Without a mutant: either one of the two limits of readBundleFile (the size check on the opened descriptor, and the read that stops at
// limit + 1 bytes), since each refuses these files with the same code, reading at most limit + 1 bytes, when the other is gone.
const MUTATIONS = {
  "a bundle file need not be a regular file": ["if (!info.isFile()) throw new NotRegularFile(NOT_REGULAR);", ""],
  "a bundle file is opened so that a FIFO blocks": ["constants.O_RDONLY | (constants.O_NONBLOCK ?? 0) | (constants.O_NOCTTY ?? 0)", "constants.O_RDONLY"],
  "a token file of any size is read": ["readBundleFile(join(tsaDir, name), MAX_TOKEN_BYTES)", "readBundleFile(join(tsaDir, name), Infinity)"],
  "publication.json of any size is read": ["readBundleFile(path, limits.MAX_PUBLICATION_BYTES)", "readBundleFile(path, Infinity)"],
  "a record file of any size is read": ["readBundleFile(itemPath, limits.MAX_ITEM_BYTES)", "readBundleFile(itemPath, Infinity)"],
  "a proof file of any size is read": ["readBundleFile(proofPath, MAX_PROOF_BYTES)", "readBundleFile(proofPath, Infinity)"],
  "a refused record file is called too large": ["if (error instanceof FileTooLarge) fail(item, \"TOO_LARGE\", \"record file\");", "if (true) fail(item, \"TOO_LARGE\", \"record file\");"],
  "a refused token is called too large": ["evidence.push({ name: label, verified: null, reason: error instanceof FileTooLarge ? \"SIZE\" : \"UNREADABLE\" });", "evidence.push({ name: label, verified: null, reason: \"SIZE\" });"],
};

for (const [name, [oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, () => {
    assert.ok(specialDeviations(mutantSourceDir("src/verify.mjs", oldText, newText)).length > 0, "these scenarios do not notice this change");
  });
}
