import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { checkLayer, checkPair, jaWidth, overridesFor, applyOverrides, strayLatin, toSrt, type Layers } from "../src/captions.ts";
import { COPY } from "../src/copy.ts";
import { fillTokens } from "../src/format.ts";
import { C10, C30, COUNTER_SECONDS, CUT10_FRAMES, CUT30_FRAMES, MAIN_FRAMES } from "../src/timing.ts";
import { PUBLIC, SRC, read } from "./helpers.ts";
import { existsSync } from "node:fs";

const EN = JSON.parse(read(join(SRC, "captions.en.json"))) as Layers;
const JA = JSON.parse(read(join(SRC, "subtitles.ja.json"))) as Layers;
const drills = existsSync(join(PUBLIC, "record", "drill.json")) ? (JSON.parse(read(join(PUBLIC, "record", "drill.json"))) as unknown[]).length : 16;
const TOKENS = { drills: String(drills), secs: String(COUNTER_SECONDS) };
const LENGTH = { main: MAIN_FRAMES, cut30: CUT30_FRAMES, cut10: CUT10_FRAMES } as const;

for (const key of ["main", "cut30", "cut10"] as const) {
  test(`${key}: every beat has an English caption and a Japanese subtitle at the same time`, () => {
    assert.deepEqual(checkPair(EN[key], JA[key]), []);
    assert.ok(EN[key].length > 0);
  });
  test(`${key}: English is at most 2 lines of 42 characters, with no overlap in time`, () => {
    assert.deepEqual(checkLayer(EN[key], "en", TOKENS, LENGTH[key]), []);
  });
  test(`${key}: Japanese is at most 2 lines of 22 full-width characters, no stray Latin letters, no overlap`, () => {
    assert.deepEqual(checkLayer(JA[key], "ja", TOKENS, LENGTH[key]), []);
  });
}

test("data-dependent beats stay within limits when the record changes them", () => {
  for (const missedCycles of [0, 3]) {
    for (const resettleCount of [true, false]) {
      const o = overridesFor({ missedCycles, resettleCount }, COPY.override);
      assert.deepEqual(checkLayer(applyOverrides(EN.main, "en", o), "en", TOKENS, MAIN_FRAMES), []);
      assert.deepEqual(checkLayer(applyOverrides(JA.main, "ja", o), "ja", TOKENS, MAIN_FRAMES), []);
    }
  }
});

test("the main cut covers every scene with a caption, and none starts before 0 or runs past the end", () => {
  const scenes = new Set(EN.main.map((b) => b.beat.split(".")[0]));
  assert.equal(scenes.size, 11);
  for (const b of [...EN.main, ...EN.cut30, ...EN.cut10]) assert.ok(b.start >= 0);
});

test("the cuts' captions fit their cut", () => {
  assert.ok(Math.max(...EN.cut30.map((b) => b.end)) <= C30.end);
  assert.ok(Math.max(...EN.cut10.map((b) => b.end)) <= C10.end);
});

test("an unknown caption token is an error, never a blank", () => {
  assert.throws(() => fillTokens("{nothing}", {}), /without a value/);
});

test("width and Latin helpers", () => {
  assert.equal(jaWidth("ab"), 1);
  assert.equal(jaWidth("星取"), 2);
  assert.deepEqual(strayLatin("PythonとNode、RYO、abc"), ["abc"]);
});

test("SRT output has one numbered block per beat", () => {
  const srt = toSrt(EN.cut10);
  assert.equal(srt.split("\n\n").filter((b) => b.trim() !== "").length, EN.cut10.length);
  assert.match(srt, /^1\n00:00:00,000 --> 00:00:02,400\n/);
});
