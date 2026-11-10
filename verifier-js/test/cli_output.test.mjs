// The command-line verifier writes its whole verdict to a pipe. A verdict of a large bundle is more than a pipe holds at once (64 KiB), and a
// process that ends with process.exit() while the rest is still waiting loses it: a reader piping the verdict to another tool got broken JSON.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const CLI = fileURLToPath(new URL("../bin/verify_bundle.mjs", import.meta.url));

function bigBundle(items) {
  const dir = mkdtempSync(join(tmpdir(), "verifier-cli-"));
  mkdirSync(join(dir, "items"));
  for (let i = 0; i < items; i++) {
    const stem = `item-${String(i).padStart(8, "0")}`;
    writeFileSync(join(dir, "items", `${stem}.canonical.json`), "{}");
    writeFileSync(join(dir, "items", `${stem}.tlog-proof`), "x");
  }
  return dir;
}

function run(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    const out = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout: Buffer.concat(out).toString("utf8") }));
  });
}

test("a verdict larger than a pipe holds arrives whole, and is valid JSON", async () => {
  const dir = bigBundle(700);
  try {
    const { code, stdout } = await run([dir]);
    assert.equal(code, 1, "a bundle that is not intact exits 1");
    assert.ok(Buffer.byteLength(stdout) > 70_000, `the verdict is large enough to matter: ${Buffer.byteLength(stdout)} bytes`);
    const verdict = JSON.parse(stdout);
    assert.equal(verdict.integrity.ok, false);
    assert.equal(Object.keys(verdict.integrity.items).length, 700, "every item is in the verdict");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a small verdict, and a refusal to run, still end with their exit codes", async () => {
  const dir = bigBundle(1);
  try {
    assert.equal((await run([dir])).code, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  assert.equal((await run([])).code, 2, "no bundle is a usage error");
  assert.equal((await run(["--nope"])).code, 2, "an unknown option is a usage error");
});
