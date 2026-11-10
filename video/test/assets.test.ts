import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { IMAGES, LOGS, SCENE_ASSETS, VIDEOS } from "../src/assets.ts";
import { manifestNames } from "../src/data.ts";
import { DEV, PUBLIC, SRC, hasReal, read } from "./helpers.ts";

const names = (): Set<string> => manifestNames(JSON.parse(read(join(PUBLIC, "real", "manifest.json"))));

test("the Real manifest exists (FILM_DEV=1 skips this while the assets are being made)", { skip: DEV }, () => {
  assert.ok(hasReal, "public/real/manifest.json is missing: run `make capture` and `make terminals`");
});

test("every Real asset a scene references is in the manifest", { skip: DEV || !hasReal }, () => {
  const listed = names();
  const missing: string[] = [];
  for (const [scene, need] of Object.entries(SCENE_ASSETS)) {
    for (const k of need.images ?? []) if (!listed.has(IMAGES[k])) missing.push(`${scene}: ${IMAGES[k]}`);
    for (const k of need.videos ?? []) if (!listed.has(VIDEOS[k])) missing.push(`${scene}: ${VIDEOS[k]}`);
    for (const k of need.logs ?? []) if (!listed.has(LOGS[k])) missing.push(`${scene}: ${LOGS[k]}`);
  }
  assert.deepEqual(missing, []);
});

test("every file the manifest names for the film is on disk", { skip: DEV || !hasReal }, async () => {
  const { existsSync } = await import("node:fs");
  const all = [...Object.values(IMAGES), ...Object.values(VIDEOS), ...Object.values(LOGS)];
  assert.deepEqual(all.filter((n) => !existsSync(join(PUBLIC, "real", n))), []);
});

test("each scene source uses the assets its entry in SCENE_ASSETS names, and no others", () => {
  for (const [scene, need] of Object.entries(SCENE_ASSETS)) {
    const file = scene.startsWith("Scene") ? join(SRC, "scenes", `${scene}.tsx`) : join(SRC, "Cuts.tsx");
    const text = read(file) + (scene === "Scene4" || scene === "Scene2" || scene === "Scene8" || scene === "Scene10" ? read(join(SRC, "rows.ts")) : "");
    const used = new Set<string>();
    for (const m of text.matchAll(/images\.(\w+)/g)) used.add(m[1] ?? "");
    for (const m of text.matchAll(/videos\.(\w+)/g)) used.add(m[1] ?? "");
    for (const m of text.matchAll(/logs\.(\w+)/g)) used.add(m[1] ?? "");
    for (const key of [...(need.images ?? []), ...(need.videos ?? []), ...(need.logs ?? [])]) assert.ok(used.has(key) || new RegExp(`film\\.${key}\\b`).test(text), `${scene} does not use ${key}`);
  }
});

test("scene sources never name a file: assets are reached by key through data.ts", () => {
  for (const f of ["Scene2", "Scene3", "Scene4", "Scene5", "Scene6", "Scene7", "Scene8", "Scene10"]) {
    assert.doesNotMatch(read(join(SRC, "scenes", `${f}.tsx`)), /\.(png|webm|log)["'`]/, f);
  }
});
