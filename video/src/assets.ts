// The Real assets the scenes name (VIDEO.md section D, asset table). Scenes refer to them by key, never by file name.

export const IMAGES = {
  receiptHead: "receipt_hero_head.png",
  receiptRead: "receipt_hero_read.png",
  receiptProve: "receipt_hero_prove.png",
  receiptModel: "receipt_hero_model.png",
  todayTop: "today_top.png",
  todayChanges: "today_changes.png",
  todayDisagree: "today_disagree.png",
  todayPhoneLight: "today_phone_light.png",
  todayPhoneDark: "today_phone_dark.png",
  verifyPage: "verify_page.png",
  verifyForgery: "verify_forgery.png",
  recordPage: "record_page.png",
  recordMissed: "record_missed.png",
  drillPage: "drill_page.png",
  judgesPage: "judges_page.png",
} as const;

export const VIDEOS = {
  todayKeyboard: "today_keyboard.webm",
} as const;

export const LOGS = {
  verifyPython: "verify_python.log",
  verifyNode: "verify_node.log",
  diff: "diff.log",
  verifyForgery: "verify_forgery.log",
  skillAddress: "skill_address.log",
  skillEnvelope: "skill_envelope.log",
  skillIndicator: "skill_indicator.log",
  resettle: "resettle.log",
} as const;

export const MANIFEST = "manifest.json";
export const HERO = "hero.json";

export type ImageKey = keyof typeof IMAGES;
export type VideoKey = keyof typeof VIDEOS;
export type LogKey = keyof typeof LOGS;

/** Which scene needs which Real asset (by key). A test checks each against the manifest and against the scene source. */
export const SCENE_ASSETS: Record<string, { images?: ImageKey[]; videos?: VideoKey[]; logs?: LogKey[] }> = {
  Scene2: { logs: ["verifyPython"] },
  Scene3: { images: ["receiptHead", "receiptRead", "receiptProve", "receiptModel", "todayDisagree"] },
  Scene4: { images: ["verifyPage", "verifyForgery"], logs: ["verifyPython", "verifyNode", "diff", "verifyForgery"] },
  Scene5: { images: ["recordPage"] },
  Scene6: {
    images: ["todayTop", "todayChanges", "todayPhoneLight", "todayPhoneDark", "judgesPage"],
    videos: ["todayKeyboard"],
  },
  Scene7: { images: ["drillPage", "recordMissed"] },
  Scene8: { logs: ["skillAddress", "skillEnvelope", "skillIndicator"] },
  Scene10: { logs: ["resettle"] },
  Cut30: { images: ["receiptProve", "todayChanges", "todayPhoneLight"], logs: ["verifyPython", "verifyNode", "skillAddress"] },
};

/** Every file the dev stand-in generator must supply when the real ones are missing. */
export const ALL_NAMES: string[] = [...Object.values(IMAGES), ...Object.values(VIDEOS), ...Object.values(LOGS)];
