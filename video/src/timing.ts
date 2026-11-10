// Every frame and time constant of the film lives here. Scene components hold no numbers of their own.
// Times are written in seconds from the start of the main cut (VIDEO.md section B) and turned into frames at 30 fps.

export const FPS = 30;
export const frames = (seconds: number): number => Math.round(seconds * FPS);

/** 240 ms eased slide, the only motion the film uses besides cuts and typing. */
export const SLIDE = frames(0.24);
export const TYPE_CPS = 40;

export const MAIN_FRAMES = 4200;
export const CUT30_FRAMES = 900;
export const CUT10_FRAMES = 300;

/** Scene boundaries of the main cut, in seconds. */
export const SCENE_SECONDS = [0, 6, 14, 26, 44, 58, 68, 86, 100, 118, 130, 140] as const;

function local<T extends Record<string, number>>(start: number, beats: T): { [K in keyof T]: number } {
  const out = {} as { [K in keyof T]: number };
  for (const key of Object.keys(beats) as (keyof T)[]) out[key] = frames((beats[key] as number) - start);
  return out;
}

/** Beats by scene, as frames from the start of that scene. The inputs are the "t" column of VIDEO.md. */
export const B = {
  s0: local(0, { number: 0, rule: 2, tick: 2.4, unknown: 3.6, mark: 5, end: 6 }),
  s1: local(6, { claim: 6, questions: 8.5, promise: 11.5, end: 14 }),
  s2: local(14, { line: 14, times: 17, tick: 19, tone: 21.5, practice: 22, tracks: 24, end: 26 }),
  s3: local(26, { kicker: 26, head: 27, read: 30, prove: 33, model: 36.5, disagree: 40, snapshot: 42.5, end: 44 }),
  s4: local(44, { diagram: 44, stamps: 46.5, verify: 48, logs: 51, forged: 54.5, buzz: 56, end: 58 }),
  s5: local(58, { settle: 58, tone: 58.2, record: 62, compare: 65, end: 68 }),
  s6: local(68, { kicker: 68, top: 69, changes: 72, keyboard: 76, phones: 80, judges: 83, stop: 85, end: 86 }),
  s7: local(86, { claim: 86, drills: 88.5, missed: 93.5, never: 97, end: 100 }),
  s8: local(100, { names: 100, gap: 103.5, address: 106, envelope: 112, indicator: 115.5, end: 118 }),
  s9: local(118, { title: 118, lines: 119.5, footer: 127, end: 130 }),
  s10: local(130, { terminal: 130, tone: 134, closing: 136, white: 139, end: 140 }),
};

/** Offsets inside a scene, in frames, that are not a "t" of their own. */
export const STEP = {
  questionGap: frames(0.6),
  dotGap: frames(0.5),
  loopLine: frames(1.5),
  trackGap: frames(0.4),
  skillGap: frames(0.2),
  limitGap: frames(1.8),
  drillGap: frames(0.25),
  holdSafety: frames(1),
  fade: SLIDE,
  rule: frames(1.2),
  stampGap: frames(0.3),
  squares: frames(1.6),
  printGap: frames(0.25),
  printLead: frames(0.3),
  cmdPause: frames(0.25),
  timesGap: frames(0.35),
  mark: frames(1),
  fellDrop: frames(0.3),
  barDraw: frames(1),
  strike: frames(0.4),
  numberLead: frames(0.5),
  trackLead: frames(0.4),
  strikeLead: frames(1),
  footerGap: frames(0),
  crossfade: frames(0.2),
};

/** Scene 6's honest counter: it runs from the first card to the action and then stops. */
export const COUNTER = { fromSec: 72, toSec: 85 } as const;
export const COUNTER_SECONDS = COUNTER.toSec - COUNTER.fromSec;

/** Where scene 6's crops live within the scene, in seconds from its start, for the pans. */
export const SCENE_BOUNDS = SCENE_SECONDS.map((s) => frames(s));

/** The 30-second cut (VIDEO.md section E), seconds from its start. */
export const C30 = {
  call: 0,
  promise: 4,
  prove: 8,
  verify: 13,
  settle: 18,
  today: 22,
  skills: 26,
  close: 29,
  end: 30,
};

/** The 10-second cut, seconds from its start. */
export const C10 = { seal: 0, stamps: 2.5, settle: 5, close: 7.5, end: 10 };

export const sceneFrom = (index: number): number => frames(SCENE_SECONDS[index] ?? 0);
export const sceneLength = (index: number): number => frames((SCENE_SECONDS[index + 1] ?? 0) - (SCENE_SECONDS[index] ?? 0));
