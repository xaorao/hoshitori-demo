import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { mutantDir } from "./mutate.mjs";

const GOLDEN = fileURLToPath(new URL("../../tests/golden/settlement/", import.meta.url));
const CASES = readdirSync(GOLDEN).filter((name) => name.endsWith(".json")).sort().map((name) => JSON.parse(readFileSync(join(GOLDEN, name), "utf8")));

// [file, old text, new text]. Each change breaks one rule of the contract; the golden cases must notice it.
const MUTATIONS = {
  "a bar is believed before its minute has closed": ["src/settlement.mjs", "if (openMs + MINUTE > oracle.serverMs)", "if (false)"],
  "a bar closing at the server clock is not yet closed": ["src/settlement.mjs", "openMs + MINUTE > oracle.serverMs", "openMs + MINUTE >= oracle.serverMs"],
  "a missing bar is final at once": ["src/settlement.mjs", 'retriesOver ? ["UNSETTLEABLE", "UNSETTLEABLE_MISSING"] : ["PENDING", "CANDLE_MISSING"]', '["UNSETTLEABLE", "UNSETTLEABLE_MISSING"]'],
  "a missing bar never becomes final": ["src/settlement.mjs", 'retriesOver ? ["UNSETTLEABLE", "UNSETTLEABLE_MISSING"] : ["PENDING", "CANDLE_MISSING"]', '["PENDING", "CANDLE_MISSING"]'],
  "unsettleable beats pending": ["src/settlement.mjs", 'needs.find(([state]) => state === "PENDING")', 'needs.find(([state]) => state === "UNSETTLEABLE")'],
  "the oracles are compared on raw prices": ["src/settlement.mjs", "const one = primaryClose.div(primaryScale);", "const one = primaryClose;"],
  "the second oracle is not scaled": ["src/settlement.mjs", "const other = secondClose.div(secondScale);", "const other = secondClose;"],
  "the agreement limit is one percent": ["src/settlement.mjs", 'Q.parse("0.005")', 'Q.parse("0.01")'],
  "the agreement limit is exclusive": ["src/settlement.mjs", ".cmp(HALF_PERCENT.mul(one)) <= 0", ".cmp(HALF_PERCENT.mul(one)) < 0"],
  "the agreement limit is a share of the second oracle": ["src/settlement.mjs", "const one = primaryClose.div(primaryScale);\n  const other = secondClose.div(secondScale);\n  return one.sub(other).abs().cmp(HALF_PERCENT.mul(one)) <= 0;", "const one = primaryClose.div(primaryScale);\n  const other = secondClose.div(secondScale);\n  return one.sub(other).abs().cmp(HALF_PERCENT.mul(other)) <= 0;"],
  "the outcome closes are not compared": ["src/settlement.mjs", "agree(outcome.close, pair.primary.multiplier, secondOutcome.close, pair.second.multiplier);", "true;"],
  "the reference closes are not compared": ["src/settlement.mjs", "agree(reference.close, pair.primary.multiplier, secondReference.close, pair.second.multiplier) &&", "true &&"],
  "a barrier ignores a disagreeing reference": ["src/settlement.mjs", "if (!agree(reference.close, pair.primary.multiplier, secondReference.close, pair.second.multiplier)) {", "if (false) {"],
  "prices are not rounded to the tick": ["src/settlement.mjs", "return tick === null ? price : Q.of(price.div(tick).roundHalfEven());", "return price;"],
  "a rounded price is kept without its tick": ["src/settlement.mjs", "Q.of(price.div(tick).roundHalfEven())", "Q.of(price.div(tick).roundHalfEven()).mul(tick).div(tick)"],
  "a tie is called a rise": ["src/settlement.mjs", 'if (order === 0) return answer("D", "VOID", null, "VOID_TIE", referenceText, outcomeText, null, flags);', ""],
  "up and down are swapped": ["src/settlement.mjs", 'order > 0 ? "1" : "0"', 'order > 0 ? "0" : "1"'],
  "the reference is the outcome candle": ["src/settlement.mjs", "const referenceOpen = origin - MINUTE;\n  const outcomeOpen", "const referenceOpen = origin;\n  const outcomeOpen"],
  "the outcome is one minute late": ["src/settlement.mjs", "const outcomeOpen = origin + horizon - MINUTE;", "const outcomeOpen = origin + horizon;"],
  "the basis flag ignores sign": ["src/settlement.mjs", "Q.parse(basis).abs().cmp(BASIS_LIMIT) > 0", "Q.parse(basis).cmp(BASIS_LIMIT) > 0"],
  "the basis limit is inclusive": ["src/settlement.mjs", "Q.parse(basis).abs().cmp(BASIS_LIMIT) > 0", "Q.parse(basis).abs().cmp(BASIS_LIMIT) >= 0"],
  "a long target touch is exclusive": ["src/settlement.mjs", "[high.cmp(target) >= 0, low.cmp(stop) <= 0]", "[high.cmp(target) > 0, low.cmp(stop) <= 0]"],
  "a long stop touch is exclusive": ["src/settlement.mjs", "[high.cmp(target) >= 0, low.cmp(stop) <= 0]", "[high.cmp(target) >= 0, low.cmp(stop) < 0]"],
  "a short target touch is exclusive": ["src/settlement.mjs", "[low.cmp(target) <= 0, high.cmp(stop) >= 0]", "[low.cmp(target) < 0, high.cmp(stop) >= 0]"],
  "a short stop touch is exclusive": ["src/settlement.mjs", "[low.cmp(target) <= 0, high.cmp(stop) >= 0]", "[low.cmp(target) <= 0, high.cmp(stop) > 0]"],
  "a short has a long's levels": ["src/settlement.mjs", "const up = forecast.side === \"LONG\";", "const up = true;"],
  "the levels are from the origin close": ["src/settlement.mjs", "const target = up ? reference.close.add(targetOffset)", "const target = up ? secondReference.close.add(targetOffset)"],
  "the scan starts a minute early": ["src/settlement.mjs", "for (let openMs = origin; openMs < origin + horizon; openMs += MINUTE) {", "for (let openMs = origin - MINUTE; openMs < origin + horizon; openMs += MINUTE) {"],
  "the scan stops a minute early": ["src/settlement.mjs", "openMs < origin + horizon; openMs += MINUTE", "openMs < origin + horizon - MINUTE; openMs += MINUTE"],
  "the scan runs a minute long": ["src/settlement.mjs", "openMs < origin + horizon; openMs += MINUTE", "openMs <= origin + horizon; openMs += MINUTE"],
  "a bar in the window need not exist": ["src/settlement.mjs", "if (found.need) return answer(\"B\", found.need[0], null, found.need[1], p0Text, null, null, flags);", "if (found.need) continue;"],
  "both levels in one minute go to the target": ["src/settlement.mjs", 'if (first === null) return answer("B", "AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", p0Text, null, openMs, flags);', 'if (first === null) return answer("B", "SETTLED", "TARGET", null, p0Text, null, openMs, flags);'],
  "finer pieces are ignored": ["src/settlement.mjs", "finer[String(openMs)] ?? []", "[]"],
  "finer pieces are read last first": ["src/settlement.mjs", "for (const [high, low] of pieces) {", "for (const [high, low] of [...pieces].reverse()) {"],
  "a finer piece touching both is taken as the target": ["src/settlement.mjs", "if (hitTarget && hitStop) return null;\n    if (hitTarget) return \"TARGET\";", "if (hitTarget) return \"TARGET\";"],
  "a stop is reported as a target": ["src/settlement.mjs", 'return answer("B", "SETTLED", "STOP", null, p0Text, null, openMs, flags);', 'return answer("B", "SETTLED", "TARGET", null, p0Text, null, openMs, flags);'],
  "the target wins a clean target-and-stop minute apart": ["src/settlement.mjs", "if (hitTarget) return answer(\"B\", \"SETTLED\", \"TARGET\"", "if (hitStop) return answer(\"B\", \"SETTLED\", \"STOP\", null, p0Text, null, openMs, flags);\n    if (hitTarget) return answer(\"B\", \"SETTLED\", \"TARGET\""],
  "flags are not sorted": ["src/settlement.mjs", "flags: [...flags].sort()", "flags: [...flags]"],
  "a void takes any reason": ["src/settlement.mjs", "!VOID_REASONS.has(reason) || ", ""],
  "a void takes any contract": ["src/settlement.mjs", ' || (contract !== "D" && contract !== "B")', ""],
  "a horizon may be shorter than an hour": ["src/settlement.mjs", "if (horizon < HOUR || horizon % MINUTE !== 0)", "if (horizon % MINUTE !== 0)"],
  "a horizon may be a part-minute": ["src/settlement.mjs", "if (horizon < HOUR || horizon % MINUTE !== 0)", "if (horizon < HOUR)"],
  "an origin may be off the minute": ["src/settlement.mjs", "if (origin < MINUTE || origin % MINUTE !== 0)", "if (origin < MINUTE)"],
  "the quote class is not compared": ["src/settlement.mjs", "if (forecast.quote_class !== pair.primary.quote) throw", "if (false) throw"],
  "a pair may cross quote classes": ["src/settlement.mjs", "primary.quote !== second.quote", "false"],
  "a side may be anything": ["src/settlement.mjs", 'if (forecast.side !== "LONG" && forecast.side !== "SHORT") throw new Invalid("a side is LONG or SHORT");', ""],
  "a zero target offset is accepted": ["src/settlement.mjs", "if (!targetOffset.isPositive() || !stopOffset.isPositive())", "if (!stopOffset.isPositive())"],
  "a zero stop offset is accepted": ["src/settlement.mjs", "if (!targetOffset.isPositive() || !stopOffset.isPositive())", "if (!targetOffset.isPositive())"],
  "a bar's range need not hold its open": ["src/settlement.mjs", "const lowest = bar.open.cmp(bar.close) <= 0 ? bar.open : bar.close;", "const lowest = bar.close;"],
  "a bar's range need not hold its close": ["src/settlement.mjs", "const highest = bar.open.cmp(bar.close) >= 0 ? bar.open : bar.close;", "const highest = bar.open;"],
  "a zero price is accepted": ["src/settlement.mjs", "if (!price.isPositive()) throw", "if (price.num < 0n) throw"],
  "a bar off the minute is accepted": ["src/settlement.mjs", 'if (openMs % MINUTE !== 0) throw new Invalid("a bar opens on a whole minute");', ""],
  "an oracle may have a zero multiplier": ["src/settlement.mjs", "if (!multiplier.isPositive() || (tick !== null && !tick.isPositive()))", "if (tick !== null && !tick.isPositive())"],
  "an oracle may have a zero tick": ["src/settlement.mjs", "if (!multiplier.isPositive() || (tick !== null && !tick.isPositive()))", "if (!multiplier.isPositive())"],
  "rounding goes half up": ["src/rational.mjs", "(twice === this.den && quotient % 2n !== 0n)", "(twice === this.den)"],
  "rounding goes half down": ["src/rational.mjs", "twice > this.den ||", "twice > this.den + 0n && false ||"],
  "negative numbers round the wrong way": ["src/rational.mjs", "if (remainder < 0n) {\n      quotient -= 1n;\n      remainder += this.den;\n    }", ""],
  "a number may be written with an exponent": ["src/rational.mjs", "/^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$/", "/^-?[0-9.eE+-]+$/"],
};

for (const [name, [file, oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`the golden cases notice: ${name}`, async () => {
    const load = mutantDir(file, oldText, newText);
    const mutated = await load("settlement.mjs");
    const noticed = CASES.some((c) => {
      let got;
      try {
        got = mutated.runCase(c);
      } catch {
        return true;
      }
      return JSON.stringify(got) !== JSON.stringify(c.expected);
    });
    assert.ok(noticed, `no golden case fails when: ${name}`);
  });
}
