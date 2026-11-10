import assert from "node:assert/strict";
import { test } from "node:test";
import {
  COMMITTED_WORDS,
  MECHANISM_WORDS,
  MODEL_CHALLENGE_METHOD,
  MODEL_CHALLENGE_NOTE,
  MODEL_OUTCOME_WORDS,
  NO_CONTROL,
  committedLine,
  failedLine,
  modelCounts,
  modelOutcome,
  pairsLine,
  shiftLine,
  sourceWords,
} from "../src/format.ts";
import { FORMAT, LoadError, MODEL_CHALLENGES, loadModelChallenges, sha256Hex } from "../src/load.ts";

// What a challenge may never be read as saying (EVIDENCE_CHALLENGE_SPEC section 1): that the model drew on the source, that the source is
// true or useless, or anything about why a market moved. Checked on every sentence of the closed sets and on every line made from them.
const NEVER = /\bused?\b|\buses\b|\busing\b|\brel(y|ied|ies) on\b|because|caus|irrelevant|ruled out|\bproves?\b|\bmarket (moved|rose|fell)|\bprice (moved|rose|fell)/i;

const BTC = {
  treated: { same_same: 16, same_changed: 3, changed_same: 1, changed_changed: 0 },
  control: { same_same: 18, same_changed: 1, changed_same: 1, changed_changed: 0 },
};

test("a sentence for every result a challenge file can hold, and no other", () => {
  assert.deepEqual(Object.keys(MODEL_OUTCOME_WORDS).sort(), [
    "DECISION_CHANGED",
    "DECISION_STABLE",
    "EFFECT_UNCERTAIN",
    "EVIDENCE_REQUIRED",
    "NOT_EVALUATED",
    "NOT_EVALUATED(structural_failure)",
    "NOT_EVALUATED(unstable_baseline)",
  ]);
  assert.deepEqual(Object.keys(MECHANISM_WORDS).sort(), ["BOTH", "GATE_BLOCKED", "MODEL_REVISED", "VARIATION_ONLY"]);
  assert.deepEqual(Object.keys(COMMITTED_WORDS).sort(), ["DOWN", "FLAT", "UP"]);
});

test("the result in words, in the spec's own words where it gives them", () => {
  assert.equal(modelOutcome("EFFECT_UNCERTAIN", null), "Too few runs to say whether taking it out changed the decision. This does not mean the source made no difference.");
  assert.equal(
    modelOutcome("NOT_EVALUATED(unstable_baseline)", null),
    "Not judged: asked again on the same evidence, the agent already gave another call often enough to hide any effect.",
  );
  assert.equal(modelOutcome("EVIDENCE_REQUIRED", null), "The agent requires this source: without it, it does not decide at all. This is a result, not a failure.");
  assert.equal(
    modelOutcome("DECISION_CHANGED", "MODEL_REVISED"),
    "Taking it out changed the decision, beyond the agent's own variation. The agent changed its mind: its own proposal moved without the source more often than when it was asked again.",
  );
  assert.equal(
    modelOutcome("DECISION_CHANGED", "GATE_BLOCKED"),
    "Taking it out changed the decision, beyond the agent's own variation. A safety rule stopped it: the agent's proposal stayed within its own variation, and a check on its answer changed the final call.",
  );
  assert.match(modelOutcome("DECISION_STABLE", null), /within 15 points either way/);
});

test("an unknown result, or a mechanism where none can be, is an error and never shown", () => {
  assert.throws(() => modelOutcome("RULED_OUT", null), /not a challenge result/);
  assert.throws(() => modelOutcome("DECISION_CHANGED", null), /a mechanism comes with a change/);
  assert.throws(() => modelOutcome("EFFECT_UNCERTAIN", "MODEL_REVISED"), /a mechanism comes with a change/);
  assert.throws(() => modelOutcome("DECISION_CHANGED", "THE_MODEL_USED_IT"), /not a mechanism/);
  assert.throws(() => committedLine("NO_CALL"), /not a committed call/);
});

test("no sentence says the model drew on the source, or anything about why a market moved", () => {
  const sentences = [
    ...Object.values(MODEL_OUTCOME_WORDS),
    ...Object.values(MECHANISM_WORDS),
    ...Object.values(COMMITTED_WORDS),
    MODEL_CHALLENGE_NOTE,
    MODEL_CHALLENGE_METHOD,
    NO_CONTROL,
    modelCounts("binance-funding", 20, BTC.treated, BTC.control),
    shiftLine("+10", "-12", "+33", "0.62"),
    failedLine(0, 60, "EFFECT_UNCERTAIN"),
    failedLine(0, 1, "EVIDENCE_REQUIRED"),
    failedLine(0, 0, "NOT_EVALUATED"),
    pairsLine(20, "Single model", "nvidia/nemotron-3.5-super-vl-120b-a12b", "0.7"),
  ];
  for (const s of sentences) assert.doesNotMatch(s, NEVER, s);
  assert.match("the model used the funding reading", NEVER, "the guard itself catches the phrase");
  assert.match("the market moved because of it", NEVER);
});

test("the decision-sensitivity note and the method", () => {
  assert.equal(
    MODEL_CHALLENGE_NOTE,
    "This measures decision sensitivity: whether this agent's call depended on the source, on this evidence. It does not say how the model reasons, that the source is true, or what the market will do.",
  );
  assert.match(MODEL_CHALLENGE_METHOD, /as a control\. The control shows how much the agent varies on its own\.$/);
});

test("the committed call, the counts of both tables, and the control always", () => {
  assert.equal(committedLine("UP"), "Committed call: up, more likely to rise than to fall.");
  assert.equal(committedLine("DOWN"), "Committed call: down, more likely to fall than to rise.");
  assert.equal(committedLine("FLAT"), "Committed call: even, as likely to rise as to fall.");
  assert.equal(
    modelCounts("binance-funding", 20, BTC.treated, BTC.control),
    "With every source the agent made this call in 19 of 20 runs; without Binance perpetual funding, in 17 of 20; asked again with every source (the control), in 19 of 20.",
  );
  assert.throws(() => modelCounts("x", 20, BTC.treated, { ...BTC.control, same_same: 17 }), /do not share their full runs/);
  assert.equal(NO_CONTROL, "No control was needed: the answer was known before any pair was asked.");
  assert.equal(sourceWords("binance-funding"), "Binance perpetual funding");
});

test("the shift with a minus sign, the failed runs out of those asked, and the pairs", () => {
  assert.equal(
    shiftLine("+10", "-12", "+33", "0.62"),
    "The share of runs making this call, with every source minus without it: +10 points (95% interval \u221212 to +33); exact test p = 0.62.",
  );
  assert.equal(shiftLine("0", "-28", "+28", "1.00"), "The share of runs making this call, with every source minus without it: 0 points (95% interval \u221228 to +28); exact test p = 1.00.");
  assert.equal(failedLine(0, 60, "EFFECT_UNCERTAIN"), "0 of 60 runs failed. A failed run stays in its pair and counts against the evidence.");
  assert.equal(failedLine(13, 60, "NOT_EVALUATED(structural_failure)"), "13 of 60 runs failed. A failed run stays in its pair and counts against the evidence.");
  assert.equal(failedLine(0, 1, "EVIDENCE_REQUIRED"), "No model was asked: the agent stops before asking when a source it requires is missing, so no run failed.");
  assert.equal(failedLine(0, 0, "NOT_EVALUATED"), "No run was asked, so none failed.");
  assert.equal(pairsLine(20, "Single model", "nvidia/x", "0.7"), "20 pairs of Single model (nvidia/x, temperature 0.7).");
  assert.equal(pairsLine(1, "Single model", "m", null), "1 pair of Single model (m, temperature left to the provider).");
});

test("the model agent's challenges are loaded only when the index lists them, and checked against its digest", async () => {
  const real = globalThis.fetch;
  const doc = '[{"symbol":"BTC"}]\n';
  const serve = async (files: Record<string, string>, body: string): Promise<void> => {
    const index = JSON.stringify({ format: FORMAT, generated_at: "2026-10-30T22:40:24.966Z", mode: "Exploratory run: not a scored period", counts: { cycles: 0, rows: 0, receipts: 0 }, files });
    globalThis.fetch = async (url: string | URL | Request) => new Response(String(url).endsWith("index.json") ? index : body, { status: 200 });
  };
  try {
    await serve({}, doc);
    assert.equal(await loadModelChallenges("./data"), null);
    const digest = await sha256Hex(new Uint8Array(new TextEncoder().encode(doc)));
    await serve({ [MODEL_CHALLENGES]: digest }, doc);
    assert.deepEqual(await loadModelChallenges("./data"), [{ symbol: "BTC" }]);
    await serve({ [MODEL_CHALLENGES]: digest }, '[{"symbol":"ETH"}]\n');
    await assert.rejects(loadModelChallenges("./data"), (e: unknown) => e instanceof LoadError && e.kind === "changed" && e.path === MODEL_CHALLENGES);
  } finally {
    globalThis.fetch = real;
  }
  assert.equal(MODEL_CHALLENGES, "model_challenges.json");
});
