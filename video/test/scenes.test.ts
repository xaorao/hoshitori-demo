import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { COPY, DIGIT_ALLOWED } from "../src/copy.ts";
import { SRC, read, sceneFiles } from "./helpers.ts";

/** Source with comments removed. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

test("no scene component holds a numeric literal or a digit in its text: numbers come through data.ts, copy.ts, theme.ts or timing.ts", () => {
  const hits: string[] = [];
  for (const file of sceneFiles()) {
    const lines = code(read(join(SRC, "scenes", file))).split("\n");
    lines.forEach((line, i) => {
      // identifiers such as Scene3 or B.s3 carry digits by name; text and numbers do not
      const bare = line.replace(/\b[A-Za-z_$][\w$]*\b/g, " ");
      if (/\d/.test(bare)) hits.push(`${file}:${i + 1}: ${line.trim()}`);
      for (const str of line.match(/"[^"]*"|`[^`]*`/g) ?? []) if (/\d/.test(str.replace(/\$\{[^}]*\}/g, "")) && !/^["`]\.\.?\//.test(str)) hits.push(`${file}:${i + 1}: digit in text ${str}`);
    });
  }
  assert.deepEqual(hits, []);
});

test("no numeric literal of three or more digits anywhere in the scene components", () => {
  for (const file of sceneFiles()) assert.doesNotMatch(code(read(join(SRC, "scenes", file))), /(?<![\w$.])\d{3,}/, file);
});

test("copy.ts carries digits only in the labelled strings", () => {
  const found: string[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === "string") {
      if (/\d/.test(value) && !DIGIT_ALLOWED.includes(value)) found.push(value);
    } else if (Array.isArray(value)) value.forEach(walk);
    else if (typeof value === "object" && value !== null) Object.values(value).forEach(walk);
  };
  walk(COPY);
  assert.deepEqual(found, []);
});

test("the seven skill names are the directories under skills/", async () => {
  const { existsSync, readdirSync } = await import("node:fs");
  const dir = join(SRC, "..", "..", "skills");
  if (!existsSync(dir)) return;
  const names = readdirSync(dir).filter((n) => !n.startsWith("."));
  assert.deepEqual([...COPY.s8.names].sort(), names.sort());
});

test("the amber accent is used in exactly the places that mean fixed in time", () => {
  // scenes never name the accent directly; it reaches them only through SealRule, the stamps and the strike (ui/)
  for (const file of sceneFiles()) assert.doesNotMatch(read(join(SRC, "scenes", file)), /colour\.amber|#b45309/, file);
});
