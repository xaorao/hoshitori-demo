// Differential-test adapter: reads one hex-encoded UTF-8 key name per line, answers 1 if accepted, 0 if rejected.
import { createInterface } from "node:readline";
import { checkKeyName } from "./note.mjs";

const reader = createInterface({ input: process.stdin });
const answers = [];
for await (const line of reader) {
  if (!line.trim()) continue;
  try {
    checkKeyName(Buffer.from(line.trim(), "hex").toString("utf8"));
    answers.push("1");
  } catch {
    answers.push("0");
  }
}
console.log(answers.join(""));
