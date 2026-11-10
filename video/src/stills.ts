// One still per scene moment into out/stills/, bundled once. Usage: node src/stills.ts [--dev] [--only name,name]
// (dev stand-ins are allowed only with --dev; every such still carries the DEV stamp)
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const dev = process.argv.includes("--dev");
const onlyIdx = process.argv.indexOf("--only");
const only = onlyIdx > 0 ? (process.argv[onlyIdx + 1] ?? "").split(",") : null;
const browserIdx = process.argv.indexOf("--browser");
const browserExecutable = browserIdx > 0 ? (process.argv[browserIdx + 1] ?? null) : null;

// seconds into the main cut, chosen where a scene's content has landed
const SHOTS: [string, number][] = [
  ["s0_seal", 3.3], ["s0_mark", 5.6],
  ["s1_questions", 11], ["s1_promise", 13],
  ["s2_loop", 25],
  ["s3_head", 29], ["s3_read", 32], ["s3_prove", 35], ["s3_model", 39], ["s3_disagree", 42], ["s3_snapshot", 43.5],
  ["s4_diagram", 47.5], ["s4_verify", 50.5], ["s4_logs", 54], ["s4_forged", 57.5],
  ["s5_settle", 61.5], ["s5_record", 64.5], ["s5_compare", 67.5],
  ["s6_top", 71], ["s6_changes", 75.5], ["s6_keyboard", 79], ["s6_phones", 82.5], ["s6_judges", 85.5],
  ["s7_claim", 88], ["s7_drills", 93], ["s7_missed", 96.5], ["s7_never", 99.5],
  ["s8_names", 103], ["s8_gap", 105], ["s8_address", 111.5], ["s8_envelope", 115], ["s8_indicator", 117.5],
  ["s9_title", 119], ["s9_lines", 126.5], ["s9_footer", 129.5],
  ["s10_check", 135.5], ["s10_close", 139.5],
];
const FPS = 30;

const serveUrl = await bundle({ entryPoint: join(root, "src", "index.ts"), publicDir: join(root, "public") });
const inputProps = dev ? { dev: true } : {};
mkdirSync(join(root, "out", "stills"), { recursive: true });
const render = async (id: string, shots: [string, number][], prefix: string): Promise<void> => {
  const composition = await selectComposition({ serveUrl, id, inputProps, ...(browserExecutable === null ? {} : { browserExecutable }) });
  for (const [name, sec] of shots) {
    if (only !== null && !only.includes(name)) continue;
    await renderStill({ composition, serveUrl, inputProps, frame: Math.min(Math.round(sec * FPS), composition.durationInFrames - 1), output: join(root, "out", "stills", `${prefix}${name}.png`), ...(browserExecutable === null ? {} : { browserExecutable }) });
    console.log(`${prefix}${name}`);
  }
};
await render("Main", SHOTS, "");
await render("Cut30", [["c30_call", 2.5], ["c30_prove", 10], ["c30_verify", 14.5], ["c30_forged", 17.4], ["c30_settle", 20.5], ["c30_today", 24], ["c30_skills", 28], ["c30_close", 29.6]], "");
await render("Cut10", [["c10_seal", 2], ["c10_stamps", 4], ["c10_settle", 6.5], ["c10_close", 9]], "");
await render("Cut10Square", [["c10sq_seal", 2], ["c10sq_stamps", 4], ["c10sq_settle", 6.5], ["c10sq_close", 9]], "");
