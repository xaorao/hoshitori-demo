import assert from "node:assert/strict";
import { test } from "node:test";
import { DESK_TABLES, changeWords, deskWord, endingLine, falsificationKey, gateWord, originsLine, plainDecimal, spentLine } from "../src/format.ts";
import { href, parse } from "../src/route.ts";

test("the Desk page has an address", () => {
  assert.deepEqual(parse("#/desk"), { page: "desk" });
  assert.equal(href({ page: "desk" }), "#/desk");
  assert.deepEqual(parse("#/desk/x"), { page: "desk", run: "x" }); // one run, opened first (a receipt links to it)
  assert.deepEqual(parse("#/desk/"), { page: "missing", path: "/desk/" });
});

// The closed sets of the Desk's records (src/hoshitori/desk_record.py, desk_gates.py, desk_menu.py, falsify.py). A word table must name every code and no other.
const CODES: Record<string, string[]> = {
  state: ["RUNNING", "STOPPED", "COMMITTED", "ABSTAINED", "FAILED", "FAILED_FINAL"],
  reason: [
    "UTILITY_BELOW_THRESHOLD", "CANNOT_FLIP_DECISION", "BUDGET_EXHAUSTED", "ALL_MENU_OPENED",
    "BELOW_MINIMUM_EDGE", "ESSENTIAL_PREMISE_UNPROVEN", "EVIDENCE_CONFLICT_UNRESOLVED", "OUT_OF_SCOPE",
    "FAILED", "FAILED_EVIDENCE", "FAILED_STALE", "MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE", "RUN_INTERRUPTED",
  ],
  route: ["RYO_SCAN", "EVENT", "WATCHLIST"],
  check: ["INDEPENDENT_PRICE_CHECK", "DERIVATIVES_CONTEXT", "RYO_TOKEN_READ", "RYO_DEEP_ANALYSIS", "MARKET_CONTEXT_OVERVIEW", "MARKET_SENTIMENT_SHIFT", "ADDRESS_FLAG_CHECK"],
  status: [
    "UNCHECKED", "MIXED", "SUPPORTED", "CONTRADICTED", "NOT_FOUND_IN_SOURCES_CHECKED", "NOT_CHECKABLE", "SOURCE_UNAVAILABLE",
    "FLAGGED", "NO_FLAG_FOUND", "INCOMPLETE", "NOT_SCREENED",
  ],
  gate: ["HAZARD_SCREEN", "GROUNDING", "REQUIRED_EVIDENCE", "STALENESS", "MINIMUM_EDGE", "FALSIFICATION", "MINIMUM_EDGE_AFTER_CUT", "RISK_KERNEL"],
  gateStatus: ["PASS", "CUT", "STOP"],
  falsification: ["PASSED", "FAILED", "NOT_FALSIFIED_ATTEMPTED"],
  outcome: ["OK", "FAILED", "NOT_STARTED"],
  error: ["DEADLINE_UNREACHABLE", "NO_ADAPTER", "NO_ASSET", "NO_INSTRUMENT", "BAD_REQUEST", "CALL_FAILED", "TIMED_OUT", "UNREADABLE", "NOT_LIVE", "BINDING_MISMATCH", "UNKNOWN_CHECK_TYPE"],
  layer: ["NONE", "DETERMINISTIC", "MOVE_CHECK", "PERSON"],
  label: ["live", "recorded", "synthetic"],
  test: ["T_ONSET", "T_MARKET", "T_SOURCE", "T_PLACEBO_TIME"],
  result: ["FAVOURS_ORIGINAL", "FAVOURS_ALTERNATIVE", "INCONCLUSIVE", "NOT_TESTABLE"],
  why: ["series_too_short", "series_misaligned", "non_finite_value", "no_volatility", "proxy_has_no_variation", "no_event_time", "uncalibrated", "no_source"],
  stance: ["LONG", "FLAT", "SHORT"],
  final: ["COMMIT", "ABSTAIN", "FAILED"],
};

test("every code of the Desk's records has a sentence and no other has", () => {
  assert.deepEqual(Object.keys(DESK_TABLES).sort(), Object.keys(CODES).sort());
  for (const [table, codes] of Object.entries(CODES)) {
    const have = DESK_TABLES[table];
    assert.ok(have !== undefined, table);
    assert.deepEqual(Object.keys(have).sort(), [...codes].sort(), table);
    for (const code of codes) {
      const word = deskWord(table, code);
      assert.ok(word.length > 0 && word === word.trim(), `${table}.${code}`);
    }
  }
});

test("a code that is not in the set is an error, never shown", () => {
  assert.throws(() => deskWord("state", "SLEEPING"), /not a Desk state: SLEEPING/);
  assert.throws(() => deskWord("nope", "X"), /not a Desk word table: nope/);
});

test("words that must not claim more than the record does", () => {
  assert.doesNotMatch(deskWord("status", "UNCHECKED"), /verified|confirmed|true/i);
  assert.match(deskWord("falsification", "PASSED"), /does not prove/i);
  assert.match(deskWord("label", "recorded"), /recorded/i);
  assert.match(deskWord("label", "live"), /live/i);
  assert.match(deskWord("result", "NOT_TESTABLE"), /not/i);
  assert.match(deskWord("gate", "FALSIFICATION"), /disprove|alternative/i);
});

test("how a run ended, in one sentence", () => {
  assert.equal(endingLine("COMMITTED", null, false), "Reached a practice call: recorded here, not time-stamped.");
  assert.equal(endingLine("ABSTAINED", "BELOW_MINIMUM_EDGE", false), "Abstained: the edge it saw was below the minimum.");
  assert.match(endingLine("FAILED", "FAILED_EVIDENCE", false), /^Failed: /);
  assert.equal(endingLine("RUNNING", null, true), "Still running: no ending has been recorded.");
  assert.match(endingLine("STOPPED", "BUDGET_EXHAUSTED", true), /^Stopped: /);
  assert.throws(() => endingLine("COMMITTED", "BELOW_MINIMUM_EDGE", false), /not a reason/);
});

test("what a run spent", () => {
  assert.equal(spentLine({ calls: 2, seconds: 0, tokens: 500 }, { calls: 16, seconds: 600, tokens: 20000 }), "2 of 16 calls, 0 of 600 seconds, 500 of 20000 tokens.");
  assert.equal(spentLine({ calls: 1, seconds: 5, tokens: 0 }, { calls: 1, seconds: 5, tokens: 0 }), "1 of 1 call, 5 of 5 seconds, 0 of 0 tokens.");
});

test("a decimal is shown without its trailing zeros, and never changed", () => {
  assert.equal(plainDecimal("0.030000"), "0.03");
  assert.equal(plainDecimal("0.6000"), "0.6");
  assert.equal(plainDecimal("0.62"), "0.62");
  assert.equal(plainDecimal("0.000000"), "0");
  assert.equal(plainDecimal("1.0"), "1");
  assert.equal(plainDecimal("0"), "0");
  assert.throws(() => plainDecimal("1e-2"), /not a decimal/);
  assert.throws(() => plainDecimal(""), /not a decimal/);
});

test("the label is one short word, so it never widens a phone screen", () => {
  assert.equal(deskWord("label", "live"), "Live");
  assert.equal(deskWord("label", "recorded"), "Recorded");
  assert.equal(deskWord("label", "synthetic"), "Synthetic");
});

test("the origins that read a part, in words", () => {
  const none = { read: [], supporting: [], contradicting: [], calls: 0 };
  assert.equal(originsLine(none), "No independent origin read this part. 0 calls.");
  assert.equal(
    originsLine({ read: ["EXCHANGE:binance", "EXCHANGE:kraken"], supporting: ["EXCHANGE:binance", "EXCHANGE:kraken"], contradicting: [], calls: 4 }),
    "2 independent origins read this part (binance, kraken): 2 support it and none contradicts it. 4 calls.",
  );
  assert.equal(
    originsLine({ read: ["EXCHANGE:binance"], supporting: ["EXCHANGE:binance"], contradicting: [], calls: 4 }),
    "1 independent origin read this part (binance): 1 supports it and none contradicts it; no second origin read it. 4 calls.",
  );
  assert.equal(
    originsLine({ read: ["EXCHANGE:binance", "EXCHANGE:kraken", "RYO"], supporting: ["EXCHANGE:binance", "RYO"], contradicting: ["EXCHANGE:kraken"], calls: 7 }),
    "3 independent origins read this part (binance, kraken, RYO): 2 support it and 1 contradicts it. 7 calls.",
  );
  assert.throws(() => originsLine({ read: ["NEWS:wire"], supporting: [], contradicting: [], calls: 1 }), /not an origin/);
});

test("what a check changed in how a part stands", () => {
  assert.equal(changeWords("UNCHECKED", "SUPPORTED", "SUPPORTED"), "first check of this part");
  assert.equal(changeWords("SUPPORTED", "SUPPORTED", "SUPPORTED"), "agrees with what was found before");
  assert.equal(changeWords("CONTRADICTED", "CONTRADICTED", "CONTRADICTED"), "agrees with what was found before");
  assert.equal(changeWords("SUPPORTED", "CONTRADICTED", "MIXED"), "disagrees with what was found before");
  assert.equal(changeWords("SUPPORTED", "SOURCE_UNAVAILABLE", "SUPPORTED"), "could not be read: no change");
  assert.equal(changeWords("SUPPORTED", "NOT_CHECKABLE", "SUPPORTED"), "could not read this part: no change");
  assert.equal(changeWords("NOT_FOUND_IN_SOURCES_CHECKED", "SUPPORTED", "SUPPORTED"), "before: not found in the sources checked");
  assert.equal(changeWords("NOT_FOUND_IN_SOURCES_CHECKED", "NOT_FOUND_IN_SOURCES_CHECKED", "NOT_FOUND_IN_SOURCES_CHECKED"), "no change");
});

test("an address screen is not read as a part of the claim", () => {
  assert.equal(deskWord("check", "ADDRESS_FLAG_CHECK"), "Screen of the token's contract");
  assert.equal(deskWord("status", "NO_FLAG_FOUND"), "No flag found (not a safety rating)");
  assert.equal(deskWord("status", "FLAGGED"), "Listed by a source");
  assert.equal(deskWord("status", "INCOMPLETE"), "Some sources did not answer");
  assert.equal(deskWord("status", "NOT_SCREENED"), "Not screened");
  assert.equal(deskWord("gate", "HAZARD_SCREEN"), "No source lists the token's contract");
  assert.equal(changeWords("UNCHECKED", "NO_FLAG_FOUND", "NO_FLAG_FOUND", "ADDRESS_FLAG_CHECK"), "screened before the claim");
  assert.equal(changeWords("UNCHECKED", "SUPPORTED", "SUPPORTED", "INDEPENDENT_PRICE_CHECK"), "first check of this part");
  assert.equal(changeWords("UNCHECKED", "SUPPORTED", "SUPPORTED"), "first check of this part");
});

test("a falsification whose tests were not all run never reads as a lost test", () => {
  assert.equal(falsificationKey({ outcome: "FAILED", completed: false, label: "NOT_FALSIFIED_ATTEMPTED" }), "NOT_FALSIFIED_ATTEMPTED");
  assert.equal(falsificationKey({ outcome: "FAILED", completed: true, label: null }), "FAILED");
  assert.equal(falsificationKey({ outcome: "PASSED", completed: true, label: null }), "PASSED");
  assert.equal(deskWord("falsification", falsificationKey({ outcome: "FAILED", completed: false, label: "NOT_FALSIFIED_ATTEMPTED" })), "The tests could not all be run, so the size was cut");
});

test("a gate that changed nothing is never called passed, and says when there was nothing to cut", () => {
  assert.equal(gateWord("PASS", "0"), "Nothing to cut");
  assert.equal(gateWord("PASS", "0.03"), "No change");
  assert.equal(gateWord("CUT", "0.03"), "Cut the size");
  assert.equal(gateWord("STOP", "0"), "Stopped the call");
});
