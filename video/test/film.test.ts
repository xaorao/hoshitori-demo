import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { existsSync } from "node:fs";
import { loadFilm } from "../src/data.ts";
import { rankHeroCandidates } from "../src/select_hero.ts";
import { addressRows, indicatorRows, resettleRows, verifierRows } from "../src/rows.ts";
import { planTerminal } from "../src/logs.ts";
import { B, FPS, STEP, TYPE_CPS } from "../src/timing.ts";
import { mainEvents } from "../src/sound.ts";
import { BUILD, DEV, PUBLIC, film, fsReader, hasRecord, hasReal, read } from "./helpers.ts";

const live = hasRecord && (hasReal || DEV);
const opts = { skip: live ? false : "needs public/record and public/real (or FILM_DEV=1 with stand-ins)" };

test("the data door refuses to render without the Real manifest unless FILM_DEV is set", { skip: hasReal ? "the manifest exists here" : false }, async () => {
  await assert.rejects(loadFilm(fsReader, BUILD, {}), /manifest\.json is missing/);
});

test("the data door refuses when a scene's asset is not in the manifest", { skip: !hasReal ? "no manifest" : false }, async () => {
  const stub = { ...fsReader, async json(path: string) { const v = await fsReader.json(path); return path === "real/manifest.json" ? { files: [{ file: "today_top.png" }] } : v; } };
  await assert.rejects(loadFilm(stub, BUILD, {}), /not in the manifest/);
});

test("a real render needs a commit and a record id", opts, async () => {
  if (DEV) return;
  const nothing = { commit: null, recordSha256: null, renderedAt: null };
  const hasProvenance = JSON.stringify(JSON.parse(read(join(PUBLIC, "real", "manifest.json")))).includes("record_id");
  if (hasProvenance) await assert.rejects(loadFilm(fsReader, nothing, {}), /build metadata is incomplete/);
});

test("the hero is the call the deterministic rule names, and its receipt can carry scenes 0, 2 and 5", opts, async () => {
  const f = await film();
  const rows = (await fsReader.json("record/rows.json")) as Parameters<typeof rankHeroCandidates>[0];
  const first = rankHeroCandidates(rows)[0];
  assert.ok(first);
  if (existsSync(join(PUBLIC, "real", "hero.json"))) assert.equal(f.hero.row.forecast_id, JSON.parse(read(join(PUBLIC, "real", "hero.json"))).forecast_id);
  assert.equal(f.hero.row.state, "Settled");
  assert.ok(f.hero.invalidations.length > 0);
  assert.equal(f.hero.settlement.state, "SETTLED");
  for (const s of ["Evidence frozen", "Decided", "Committed", "Settled"]) assert.ok(f.hero.timeline.some((t) => t.state === s), s);
  assert.ok(f.hero.committed_at);
});

test("every failure drill held (scene 7 says so) and there are time-stamp operators to stamp", opts, async () => {
  const f = await film();
  assert.ok(f.drills.length > 0 && f.drills.every((d) => d.held));
  assert.ok(f.operators.length >= 2);
});

test("the forgery is the field p edited, as the product states it", opts, async () => {
  const f = await film();
  assert.equal(f.forgery.field, "p");
  assert.notEqual(f.forgery.was, f.forgery.now);
});

test("a real render's logs all carry an exit code; the refused forgery is non-zero and the verifiers are zero", opts, async () => {
  const f = await film();
  if (DEV) return;
  for (const [k, log] of Object.entries(f.logs)) assert.notEqual(log.exit, null, k);
  assert.equal(f.logs.verifyPython.exit, 0);
  assert.equal(f.logs.verifyNode.exit, 0);
  assert.notEqual(f.logs.verifyForgery.exit, 0);
});

test("every terminal plays inside its scene, with its last line held for a second", opts, async () => {
  const f = await film();
  const cases: [string, ReturnType<typeof verifierRows>, number][] = [
    ["scene 4 logs", verifierRows(f), (B.s4.forged - B.s4.logs) / FPS],
    ["scene 8 address", addressRows(f.logs.skillAddress), (B.s8.envelope - B.s8.address) / FPS],
    ["scene 8 indicator", indicatorRows(f.logs.skillIndicator), (B.s8.end - B.s8.indicator) / FPS],
    ["scene 10", resettleRows(f), (B.s10.closing - B.s10.terminal) / FPS],
  ];
  for (const [name, rows, seconds] of cases) {
    assert.ok(rows.length > 0, `${name} has no rows`);
    const plan = planTerminal(rows, seconds, TYPE_CPS, FPS, STEP.cmdPause, STEP.printLead, STEP.holdSafety);
    assert.ok(plan.total <= Math.floor(seconds * FPS) - STEP.holdSafety + STEP.cmdPause, `${name}: ends at ${plan.total} of ${seconds * FPS}`);
    assert.ok(plan.speed <= 2.5, `${name}: typing sped up ${plan.speed}x`);
  }
});

test("the skill terminal never calls an address safe", opts, async () => {
  const f = await film();
  const rows = addressRows(f.logs.skillAddress);
  assert.ok(rows.some((r) => r.kind === "result" && /not a safety rating/i.test(r.text)));
  assert.ok(rows.every((r) => !/\bis safe\b/i.test(r.text)));
});

test("the re-settle line is the log's own, or there is no number at all", opts, async () => {
  const f = await film();
  if (f.resettle === null) assert.equal(resettleRows(f).length, 2);
  else assert.equal(f.resettle.line, f.logs.resettle.lines[f.logs.resettle.lines.length - 1]?.trim());
});

test("sound events lie inside the main cut", opts, async () => {
  const f = await film();
  for (const e of mainEvents(f)) assert.ok(e.frame >= 0 && e.frame < 4200, `${e.kind} at ${e.frame}`);
});

test("the generated sounds exist when `make sound` has run", { skip: existsSync(join(PUBLIC, "sound", "room.wav")) ? false : "run `make sound`" }, () => {
  for (const n of ["tick", "tone", "buzz", "key", "room"]) assert.ok(existsSync(join(PUBLIC, "sound", `${n}.wav`)), n);
});
