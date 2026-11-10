import { test } from "node:test";
import assert from "node:assert/strict";
import { callKicker, clip, clock, counter, count, dateOnly, dots, fillTokens, horizon, largestDisagreement, money2, plainDecimal, priceMove, roundDecimal, signed2, stamp, upPercent } from "../src/format.ts";

test("prices keep their digits and group thousands, as the product does", () => {
  assert.equal(money2("75189.70000000"), "75,189.70");
  assert.equal(priceMove("75189.70000000", "74932.01000000"), "75,189.70 → 74,932.01");
  assert.equal(roundDecimal("98892.5875", 2), "98892.59");
  assert.equal(plainDecimal("0.030000"), "0.03");
  assert.throws(() => money2("1e5"), /not a decimal/);
});

test("the interval ends use a true minus", () => {
  assert.equal(signed2("-0.1622"), "−0.16");
  assert.equal(signed2("0.1740"), "+0.17");
});

test("times", () => {
  assert.equal(clock("2026-11-09T16:09:14.827Z"), "16:09:14");
  assert.equal(stamp("2026-11-09T16:09:14.827Z"), "2026-11-09 16:09:14 UTC");
  assert.equal(dateOnly("2026-11-09T16:09:14.827Z"), "2026-11-09");
  assert.throws(() => clock("yesterday"), /not a time/);
  assert.equal(counter(13), "0:13");
  assert.equal(counter(75), "1:15");
});

test("hours and counts are worded like web/src/format.ts", () => {
  assert.equal(horizon(1), "1 hour");
  assert.equal(horizon(4), "4 hours");
  assert.equal(count(1, "call"), "1 call");
  assert.equal(count(60, "shared call"), "60 shared calls");
  assert.equal(callKicker({ symbol: "BTC", hours: 4, arm_name: "Single model" }), "BTC · 4 hours · Single model");
  assert.equal(dots("a", 1, "b"), "a · 1 · b");
});

test("chance parsing", () => {
  assert.equal(upPercent("64% up"), 64);
  assert.equal(upPercent("target first 4%, stop first 6%, neither 90%"), null);
  assert.equal(upPercent(null), null);
});

test("disagreement follows the product's rule", () => {
  const row = (arm_name: string, chance: string, symbol = "LINK") => ({ contract: "D", symbol, hours: 4, arm_name, chance, forecast_id: `${arm_name}${symbol}` });
  const d = largestDisagreement([row("Rules", "50% up"), row("Single model", "54% up")]);
  assert.equal(d, null, "a gap under six points is not a disagreement");
  const e = largestDisagreement([row("Rules", "42% up", "DOGE"), row("Single model", "62% up", "DOGE"), row("Debate", "45% up", "DOGE")]);
  assert.equal(e?.gap, 20);
  assert.equal(e?.low.arm_name, "Rules");
});

test("clipping and tokens", () => {
  assert.equal(clip("abcdef", 4), "abc…");
  assert.equal(clip("abc", 4), "abc");
  assert.equal(fillTokens("{a} and {b}", { a: "1", b: "2" }), "1 and 2");
});
