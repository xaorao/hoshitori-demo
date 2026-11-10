// Writes src/build.json: the commit, the record id (sha256 of the tar) and the render time that scene 9's footer prints.
// Run with: node src/meta.ts <record.tar.gz> <repo root>
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const [pack, root] = [process.argv[2], process.argv[3]];
if (pack === undefined || root === undefined) throw new Error("usage: node src/meta.ts <record.tar.gz> <repo root>");
let commit: string | null = null;
try {
  commit = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
} catch {
  commit = null;
}
const recordSha256 = createHash("sha256").update(readFileSync(pack)).digest("hex");
const renderedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
writeFileSync(join(here, "build.json"), `${JSON.stringify({ commit, recordSha256, renderedAt }, null, 2)}\n`);
console.log(`build.json: commit ${commit ?? "none"}, record ${recordSha256.slice(0, 12)}…, ${renderedAt}`);
