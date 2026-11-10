// The hero call, chosen by rule and never by hand (VIDEO.md section 0): settled, level C3, contract D (up or down), the chance farthest
// from 50 %, the earliest settled, and a receipt that exists. A re-pack re-renders without edits.

import { upPercent } from "./format.ts";

export type HeroRow = {
  forecast_id: string;
  state: string;
  level: string;
  contract: string;
  chance: string | null;
  settled_at: string | null;
};

/** Candidates in the order the rule prefers them. */
export function rankHeroCandidates(rows: readonly HeroRow[]): HeroRow[] {
  const eligible = rows.filter((r) => r.state === "Settled" && r.level === "C3" && r.contract === "D" && upPercent(r.chance) !== null && r.settled_at !== null);
  return [...eligible].sort((a, b) => {
    const da = Math.abs((upPercent(a.chance) ?? 50) - 50);
    const db = Math.abs((upPercent(b.chance) ?? 50) - 50);
    if (da !== db) return db - da;
    const sa = a.settled_at ?? "";
    const sb = b.settled_at ?? "";
    if (sa !== sb) return sa < sb ? -1 : 1;
    return a.forecast_id < b.forecast_id ? -1 : 1;
  });
}

/** The first candidate whose receipt passes `usable` (it has invalidations and a settlement). */
export async function selectHero(rows: readonly HeroRow[], usable: (id: string) => Promise<boolean>): Promise<string> {
  for (const c of rankHeroCandidates(rows)) {
    if (await usable(c.forecast_id)) return c.forecast_id;
  }
  throw new Error("no settled C3 up-or-down call with a usable receipt in the record");
}
