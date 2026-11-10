import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { runCase } from "../src/settlement.mjs";

const GOLDEN = fileURLToPath(new URL("../../tests/golden/settlement/", import.meta.url));
const names = readdirSync(GOLDEN).filter((name) => name.endsWith(".json")).sort();

test("the golden set is large enough", () => {
  assert.ok(names.length >= 60, `${names.length} cases`);
});

for (const name of names) {
  test(`golden ${name}`, () => {
    const c = JSON.parse(readFileSync(join(GOLDEN, name), "utf8"));
    assert.deepEqual(runCase(c), c.expected, `${c.id} (contract ${c.section})`);
  });
}
