import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { B, C10, C30, COUNTER, COUNTER_SECONDS, CUT10_FRAMES, CUT30_FRAMES, FPS, MAIN_FRAMES, SCENE_SECONDS, SLIDE, frames, sceneFrom, sceneLength } from "../src/timing.ts";
import { SRC, read } from "./helpers.ts";

test("compositions: Main 4200 frames, Cut30 900, Cut10 300 (also 1080x1080) at 30 fps", () => {
  assert.equal(FPS, 30);
  assert.equal(MAIN_FRAMES, 4200);
  assert.equal(CUT30_FRAMES, 900);
  assert.equal(CUT10_FRAMES, 300);
  const root = read(join(SRC, "Root.tsx"));
  const grab = (id: string): string => new RegExp(`<Composition id="${id}"[^>]*?/>`, "s").exec(root)?.[0] ?? "";
  assert.match(grab("Main"), /width=\{layout\.width\} height=\{layout\.height\} fps=\{FPS\} durationInFrames=\{MAIN_FRAMES\}/);
  assert.match(grab("Cut30"), /durationInFrames=\{CUT30_FRAMES\}/);
  assert.match(grab("Cut10"), /durationInFrames=\{CUT10_FRAMES\}/);
  assert.match(grab("Cut10Square"), /width=\{layout\.height\} height=\{layout\.height\} fps=\{FPS\} durationInFrames=\{CUT10_FRAMES\}/);
});

test("the scene table is VIDEO.md's timing table, frame for frame", () => {
  assert.equal(SCENE_SECONDS.length, 12);
  assert.equal(sceneFrom(0), 0);
  assert.equal(sceneFrom(4), 1320);
  assert.equal(sceneFrom(10), 3900);
  assert.equal(sceneFrom(10) + sceneLength(10), MAIN_FRAMES);
  let total = 0;
  for (let i = 0; i < 11; i += 1) total += sceneLength(i);
  assert.equal(total, MAIN_FRAMES);
});

test("every beat sits inside its scene, in order", () => {
  const scenes = Object.values(B);
  scenes.forEach((beats, i) => {
    const length = sceneLength(i);
    for (const [name, at] of Object.entries(beats)) assert.ok(at >= 0 && at <= length, `scene ${i} beat ${name} at ${at} of ${length}`);
  });
});

test("slides are 240 ms", () => {
  assert.equal(SLIDE, frames(0.24));
  assert.equal(SLIDE, 7);
});

test("the scene 6 counter runs 13 seconds of screen time and stops where the action is", () => {
  assert.equal(COUNTER_SECONDS, 13);
  assert.equal(frames(COUNTER.fromSec) - sceneFrom(6), B.s6.changes);
  assert.equal(frames(COUNTER.toSec) - sceneFrom(6), B.s6.stop);
});

test("the cuts end where their compositions end", () => {
  assert.equal(frames(C30.end), CUT30_FRAMES);
  assert.equal(frames(C10.end), CUT10_FRAMES);
});
