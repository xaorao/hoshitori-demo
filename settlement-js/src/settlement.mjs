// Settlement of the price contracts D (directional) and B (barrier), written from the contract text (sections 2, 3, 7, 14 and 17) in a different
// language from the Python module, with no shared code. A settlement is a pure function of the forecast, the two oracles' one-minute candles,
// their servers' clocks and a flag that says the retries for missing candles are over.
import { Invalid, Q } from "./rational.mjs";

export { Invalid };

const MINUTE = 60_000;
const HOUR = 3_600_000;
const HALF_PERCENT = Q.parse("0.005");
const BASIS_LIMIT = Q.parse("0.003");
export const VOID_REASONS = new Set(["VOID_TIE", "VOID_GAP", "VOID_DELISTED", "VOID_OUTAGE", "VOID_REDENOMINATION", "VOID_TOKEN_EVENT"]);

function wholeMs(value, what) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Invalid(`${what} is a whole number of milliseconds`);
  return value;
}

function checkHorizon(horizon) {
  wholeMs(horizon, "the horizon");
  if (horizon < HOUR || horizon % MINUTE !== 0) throw new Invalid("a horizon is whole minutes, at least an hour");
  return horizon;
}

function checkOrigin(origin) {
  wholeMs(origin, "the origin");
  if (origin < MINUTE || origin % MINUTE !== 0) throw new Invalid("the origin is a whole minute after the epoch");
  return origin;
}

// --- inputs ---------------------------------------------------------------------------------------------------------------------------
export function readBar(row) {
  const [openMs, open, high, low, close] = row;
  wholeMs(openMs, "a bar's open time");
  if (openMs % MINUTE !== 0) throw new Invalid("a bar opens on a whole minute");
  const bar = { openMs, text: { close }, open: Q.parse(open), high: Q.parse(high), low: Q.parse(low), close: Q.parse(close) };
  for (const price of [bar.open, bar.high, bar.low, bar.close]) if (!price.isPositive()) throw new Invalid("a price is positive");
  const lowest = bar.open.cmp(bar.close) <= 0 ? bar.open : bar.close;
  const highest = bar.open.cmp(bar.close) >= 0 ? bar.open : bar.close;
  if (bar.low.cmp(lowest) > 0 || bar.high.cmp(highest) < 0) throw new Invalid("a bar's range holds its open and close");
  return bar;
}

export function readOracle(data) {
  const multiplier = Q.parse(data.multiplier);
  const tick = data.tick === null ? null : Q.parse(data.tick);
  if (!multiplier.isPositive() || (tick !== null && !tick.isPositive())) throw new Invalid("an oracle has a positive multiplier and tick");
  const serverMs = wholeMs(data.server_time_ms, "a server time");
  const bars = new Map();
  for (const row of data.bars) {
    const bar = readBar(row);
    bars.set(bar.openMs, bar);
  }
  return { quote: data.quote_class, multiplier, tick, serverMs, bars };
}

export function readPair(data) {
  const primary = readOracle(data.primary);
  const second = readOracle(data.second);
  if (!data.pair_id || primary.quote !== second.quote) throw new Invalid("the two oracles of a pair share a quote class");
  return { id: data.pair_id, primary, second };
}

// --- reading bars ---------------------------------------------------------------------------------------------------------------------
// A bar can be used when it exists and its minute had ended by the venue's own clock. Otherwise the answer is a state, not a price.
function lookup(oracle, openMs, retriesOver) {
  const bar = oracle.bars.get(openMs);
  if (bar === undefined) return { need: retriesOver ? ["UNSETTLEABLE", "UNSETTLEABLE_MISSING"] : ["PENDING", "CANDLE_MISSING"] };
  if (openMs + MINUTE > oracle.serverMs) return { need: ["PENDING", "BAR_NOT_CLOSED"] };
  return { bar };
}

function readAll(requests, retriesOver) {
  const bars = [];
  const needs = [];
  for (const [oracle, openMs] of requests) {
    const found = lookup(oracle, openMs, retriesOver);
    if (found.bar) bars.push(found.bar);
    else needs.push(found.need);
  }
  // an unclosed bar can still change the answer, so it is reported before a bar that will never come
  const waiting = needs.find(([state]) => state === "PENDING");
  return { bars, need: waiting ?? needs[0] ?? null };
}

function agree(primaryClose, primaryScale, secondClose, secondScale) {
  const one = primaryClose.div(primaryScale);
  const other = secondClose.div(secondScale);
  return one.sub(other).abs().cmp(HALF_PERCENT.mul(one)) <= 0;
}

function rounded(price, tick) {
  return tick === null ? price : Q.of(price.div(tick).roundHalfEven());
}

function flagsFor(basis) {
  return basis !== null && basis !== undefined && Q.parse(basis).abs().cmp(BASIS_LIMIT) > 0 ? ["BASIS_STRESS"] : [];
}

function answer(contract, state, outcome, reason, p0, price, touch, flags) {
  return { contract, state, outcome, reason, p0, outcome_price: price, touch_open_ms: touch, flags: [...flags].sort() };
}

function checkIdentity(forecast, pair) {
  if (forecast.quote_class !== pair.primary.quote) throw new Invalid("the forecast and its pair disagree on the quote class");
}

// --- contract D -----------------------------------------------------------------------------------------------------------------------
export function settleDirectional(forecast, pairData, { retriesExhausted, basis = null }) {
  const pair = readPair(pairData);
  checkIdentity(forecast, pair);
  const origin = checkOrigin(forecast.origin_ms);
  const horizon = checkHorizon(forecast.horizon_ms);
  const referenceOpen = origin - MINUTE;
  const outcomeOpen = origin + horizon - MINUTE;
  const flags = flagsFor(basis);
  const requests = [pair.primary, pair.second].flatMap((oracle) => [referenceOpen, outcomeOpen].map((openMs) => [oracle, openMs]));
  const { bars, need } = readAll(requests, retriesExhausted);
  if (need) return answer("D", need[0], null, need[1], null, null, null, flags);
  const [reference, outcome, secondReference, secondOutcome] = bars;
  const referenceText = pairData.primary.bars.find((row) => row[0] === referenceOpen)[4];
  const outcomeText = pairData.primary.bars.find((row) => row[0] === outcomeOpen)[4];
  const bothAgree =
    agree(reference.close, pair.primary.multiplier, secondReference.close, pair.second.multiplier) &&
    agree(outcome.close, pair.primary.multiplier, secondOutcome.close, pair.second.multiplier);
  if (!bothAgree) return answer("D", "UNSETTLEABLE", null, "UNSETTLEABLE_DISAGREE", referenceText, outcomeText, null, flags);
  const before = rounded(reference.close, pair.primary.tick);
  const after = rounded(outcome.close, pair.primary.tick);
  const order = after.cmp(before);
  if (order === 0) return answer("D", "VOID", null, "VOID_TIE", referenceText, outcomeText, null, flags);
  return answer("D", "SETTLED", order > 0 ? "1" : "0", null, referenceText, outcomeText, null, flags);
}

// --- contract B -----------------------------------------------------------------------------------------------------------------------
function touches(side, high, low, target, stop) {
  return side === "LONG" ? [high.cmp(target) >= 0, low.cmp(stop) <= 0] : [low.cmp(target) <= 0, high.cmp(stop) >= 0];
}

function orderWithin(side, pieces, target, stop) {
  for (const [high, low] of pieces) {
    const [hitTarget, hitStop] = touches(side, Q.parse(high), Q.parse(low), target, stop);
    if (hitTarget && hitStop) return null;
    if (hitTarget) return "TARGET";
    if (hitStop) return "STOP";
  }
  return null;
}

export function settleBarrier(forecast, pairData, { retriesExhausted, finer = {}, basis = null }) {
  const pair = readPair(pairData);
  checkIdentity(forecast, pair);
  const origin = checkOrigin(forecast.origin_ms);
  const horizon = checkHorizon(forecast.horizon_ms);
  if (forecast.side !== "LONG" && forecast.side !== "SHORT") throw new Invalid("a side is LONG or SHORT");
  const targetOffset = Q.parse(forecast.target_offset);
  const stopOffset = Q.parse(forecast.stop_offset);
  if (!targetOffset.isPositive() || !stopOffset.isPositive()) throw new Invalid("barrier offsets are positive distances");
  const flags = flagsFor(basis);
  const referenceOpen = origin - MINUTE;
  const { bars, need } = readAll([[pair.primary, referenceOpen], [pair.second, referenceOpen]], retriesExhausted);
  if (need) return answer("B", need[0], null, need[1], null, null, null, flags);
  const [reference, secondReference] = bars;
  const p0Text = pairData.primary.bars.find((row) => row[0] === referenceOpen)[4];
  if (!agree(reference.close, pair.primary.multiplier, secondReference.close, pair.second.multiplier)) {
    return answer("B", "UNSETTLEABLE", null, "UNSETTLEABLE_DISAGREE", p0Text, null, null, flags);
  }
  const up = forecast.side === "LONG";
  const target = up ? reference.close.add(targetOffset) : reference.close.sub(targetOffset);
  const stop = up ? reference.close.sub(stopOffset) : reference.close.add(stopOffset);
  for (let openMs = origin; openMs < origin + horizon; openMs += MINUTE) {
    const found = lookup(pair.primary, openMs, retriesExhausted);
    if (found.need) return answer("B", found.need[0], null, found.need[1], p0Text, null, null, flags);
    const [hitTarget, hitStop] = touches(forecast.side, found.bar.high, found.bar.low, target, stop);
    if (hitTarget && hitStop) {
      const first = orderWithin(forecast.side, finer[String(openMs)] ?? [], target, stop);
      if (first === null) return answer("B", "AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", p0Text, null, openMs, flags);
      return answer("B", "SETTLED", first, null, p0Text, null, openMs, flags);
    }
    if (hitTarget) return answer("B", "SETTLED", "TARGET", null, p0Text, null, openMs, flags);
    if (hitStop) return answer("B", "SETTLED", "STOP", null, p0Text, null, openMs, flags);
  }
  return answer("B", "SETTLED", "TIMEOUT", null, p0Text, null, null, flags);
}

// --- voids ----------------------------------------------------------------------------------------------------------------------------
export function voidSettlement(contract, reason) {
  if (!VOID_REASONS.has(reason) || (contract !== "D" && contract !== "B")) throw new Invalid("a void has a reason from the closed list");
  return answer(contract, "VOID", null, reason, null, null, null, []);
}

// --- a golden case or a corpus line, in the form the files use -----------------------------------------------------------------------------
export function runCase(c) {
  try {
    if (c.kind === "void") return voidSettlement(c.contract, c.expected.reason);
    if (c.kind === "directional") return settleDirectional(c.forecast, c.pair, { retriesExhausted: c.retries_exhausted, basis: c.basis });
    return settleBarrier(c.forecast, c.pair, { retriesExhausted: c.retries_exhausted, finer: c.finer, basis: c.basis });
  } catch (error) {
    if (error instanceof Invalid) return { error: "invalid" };
    throw error;
  }
}
