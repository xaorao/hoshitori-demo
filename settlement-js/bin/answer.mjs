#!/usr/bin/env node
// hoshitori-settle: read cases (a JSON file per case in a directory, or JSON lines on stdin with "-") and print one JSON answer per case.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runCase } from "../src/settlement.mjs";

const source = process.argv[2];
if (!source) {
  console.error("hoshitori-settle: a directory of cases, or - for JSON lines on stdin");
  process.exit(2);
}
let cases;
if (source === "-") {
  cases = readFileSync(0, "utf8").split("\n").filter((line) => line.trim() !== "").map((line) => JSON.parse(line));
} else {
  cases = readdirSync(source).filter((name) => name.endsWith(".json")).sort().map((name) => JSON.parse(readFileSync(join(source, name), "utf8")));
}
for (const c of cases) process.stdout.write(`${JSON.stringify({ id: c.id, answer: runCase(c) })}\n`);
