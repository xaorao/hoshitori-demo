import { horizon } from "./format.ts";

// --- the receipt as the hub ----------------------------------------------------------------------------------------------------
/** Which way an up-or-down call leaned, from its words ("54% up"): above half up, below half down, half even; none for no call or for a
 * call that is not up-or-down (a barrier call's words name three outcomes). */
export function leanOf(chance: string | null): "up" | "down" | "even" | null {
  const m = chance === null ? null : /^(\d{1,2})% up$/.exec(chance);
  if (m === null) return null;
  const p = Number(m[1]);
  return p > 50 ? "up" : p < 50 ? "down" : "even";
}

/** The receipt in one or two sentences a person reads in five seconds: who said what, what happened, and how the other agents on the same
 * call leaned. Built only from the receipt's own fields; nothing is ranked and no word claims skill. */
export function receiptSummary(r: {
  row: {
    arm_name: string;
    symbol: string;
    hours: number;
    contract: string;
    state: string;
    chance: string | null;
    result: string | null;
  };
  links: { others: { chance: string | null }[] };
}): string {
  const { row } = r;
  const window = horizon(row.hours);
  const who = `The ${row.arm_name.toLowerCase()} agent`;
  if (row.state === "Failed" || row.state === "Too late")
    return `${who} did not finish its call on ${row.symbol} for ${window}.`;
  if (row.chance === null)
    return `${who} made no call on ${row.symbol} for ${window}.`;
  if (row.contract !== "D")
    return `${who}, target or stop on ${row.symbol} in ${window}: ${row.chance}.${
      row.result !== null ? ` It ended: ${row.result.toLowerCase()}.` : ""
    }`;
  const lean = leanOf(row.chance);
  const said = `${who} put the chance that ${row.symbol} would be higher in ${window} at ${row.chance.replace(/ up$/, "")}.`;
  let outcome = "";
  if (
    row.state === "Settled" &&
    (row.result === "Rose" || row.result === "Fell")
  ) {
    const went = row.result === "Rose" ? "up" : "down";
    outcome =
      lean === "even"
        ? ` It went ${went}; the call was even.`
        : ` It went ${went}: the call leaned the ${lean === went ? "right" : "wrong"} way.`;
  } else if (row.state === "Open" || row.state === "Deciding") {
    outcome = " Not settled yet.";
  } else if (row.state === "Void" || row.state === "Not settled") {
    outcome = " It could not be settled.";
  }
  const leaning = r.links.others
    .map((o) => leanOf(o.chance))
    .filter((l) => l !== null);
  const same = leaning.filter((l) => l === lean).length;
  const peers =
    leaning.length === 0
      ? ""
      : leaning.length === 1
        ? ` The other agent on the same call leaned ${same === 1 ? "the same way" : "differently"}.`
        : ` ${same} of the ${leaning.length} other agents on the same call leaned the same way.`;
  return `${said}${outcome}${peers}`;
}
