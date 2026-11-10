// Where the film's few sounds go: a wooden tick for each seal, a low tone for each settle, a short buzz for the refused forgery,
// soft key clicks for the keyboard run and a room-tone bed. Nothing carries information that the picture and captions do not.

import { planTerminal } from "./logs.ts";
import type { Film } from "./data.ts";
import { resettleRows } from "./rows.ts";
import { B, C10, C30, FPS, SCENE_SECONDS, STEP, TYPE_CPS, frames } from "./timing.ts";

export type Kind = "tick" | "tone" | "buzz" | "key" | "soft";
export type SoundEvent = { frame: number; kind: Kind };

export const FILES: Record<Exclude<Kind, "soft"> | "room", string> = {
  tick: "sound/tick.wav",
  tone: "sound/tone.wav",
  buzz: "sound/buzz.wav",
  key: "sound/key.wav",
  room: "sound/room.wav",
};

/** Gain per kind, 0..1, applied on top of the files' own levels. */
export const GAIN: Record<Kind | "room", number> = { tick: 1, tone: 1, buzz: 0.8, key: 0.7, soft: 0.3, room: 1 };

/** The room bed fades over the closing seconds of the main cut. */
export const ROOM_FADE_SECONDS = 4;

const SEAL_STEP = 2;
const SETTLE_STEP = 3;
const KEYS = [0.8, 1.6, 2.6];

export function mainEvents(film: Film): SoundEvent[] {
  const at = (scene: number): number => frames(SCENE_SECONDS[scene] ?? 0);
  const ev: SoundEvent[] = [];
  ev.push({ frame: at(0) + B.s0.tick, kind: "tick" });
  ev.push({ frame: at(2) + B.s2.times + SEAL_STEP * STEP.timesGap, kind: "tick" });
  ev.push({ frame: at(2) + B.s2.times + SETTLE_STEP * STEP.timesGap, kind: "tone" });
  ev.push({ frame: at(4) + B.s4.stamps, kind: "tick" });
  ev.push({ frame: at(4) + B.s4.stamps + STEP.stampGap, kind: "tick" });
  ev.push({ frame: at(4) + B.s4.forged + STEP.strikeLead, kind: "buzz" });
  ev.push({ frame: at(5) + B.s5.tone, kind: "tone" });
  for (const k of KEYS) ev.push({ frame: at(6) + B.s6.keyboard + frames(k), kind: "key" });
  film.drills.forEach((_, i) => ev.push({ frame: at(7) + B.s7.drills + STEP.fade + i * STEP.drillGap, kind: "soft" }));
  const plan = planTerminal(resettleRows(film), (B.s10.closing - B.s10.terminal) / FPS, TYPE_CPS, FPS, STEP.cmdPause, STEP.printLead, STEP.holdSafety);
  if (film.resettle !== null) ev.push({ frame: at(10) + B.s10.terminal + plan.total, kind: "tone" });
  return ev.sort((a, b) => a.frame - b.frame);
}

export function cut30Events(): SoundEvent[] {
  return [
    { frame: frames(C30.call + 2.4), kind: "tick" },
    { frame: frames(C30.verify + 0.3), kind: "tick" },
    { frame: frames(C30.verify + 3.2), kind: "buzz" },
    { frame: frames(C30.settle + 0.2), kind: "tone" },
  ];
}

export function cut10Events(): SoundEvent[] {
  return [
    { frame: frames(C10.seal + 1.0), kind: "tick" },
    { frame: frames(C10.stamps + 0.1), kind: "tick" },
    { frame: frames(C10.stamps + 0.4), kind: "tick" },
    { frame: frames(C10.settle + 0.2), kind: "tone" },
  ];
}
