import { useEffect, useState } from "react";
import type { DecisionAlert } from "./types.ts";

/** What a person follows: calls, tokens and agents. Kept in this browser only: nothing about it leaves the page, and a browser that keeps
 * nothing (a private window, blocked storage) simply follows nothing. */
export type Follows = { calls: string[]; tokens: string[]; agents: string[] };
const KEY = "hoshitori.follows";
const CHANGED = "hoshitori-follows";
const NONE: Follows = { calls: [], tokens: [], agents: [] };

function words(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string").slice(0, 500)
    : [];
}

export function readFollows(): Follows {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw === null) return NONE;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return NONE;
    const p = parsed as Record<string, unknown>;
    return {
      calls: words(p.calls),
      tokens: words(p.tokens),
      agents: words(p.agents),
    };
  } catch {
    return NONE;
  }
}

export function writeFollows(next: Follows): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage refused: the choice lasts as long as the page
  }
  window.dispatchEvent(new Event(CHANGED));
}

export function useFollows(): Follows {
  const [follows, setFollows] = useState<Follows>(readFollows);
  useEffect(() => {
    const again = (): void => setFollows(readFollows());
    window.addEventListener(CHANGED, again);
    window.addEventListener("storage", again);
    return () => {
      window.removeEventListener(CHANGED, again);
      window.removeEventListener("storage", again);
    };
  }, []);
  return follows;
}

/** The alerts that concern what is followed: a call followed, a call on a token followed, a call or a practice book of an agent followed. */
export function followed(alerts: DecisionAlert[], f: Follows): DecisionAlert[] {
  return alerts.filter(
    (a) =>
      (a.forecast_id !== null && f.calls.includes(a.forecast_id)) ||
      (a.symbol !== null && f.tokens.includes(a.symbol)) ||
      (a.arm !== null && f.agents.includes(a.arm)),
  );
}

/** Which export this browser last showed (`seen`), and the one shown before it (`before`): "since your last visit" means since `before`.
 * Exports are static, so a visit is counted by the export it showed, never by the wall clock; reloading one export changes nothing. */
export type Visits = { seen: string | null; before: string | null };
const VISITS = "hoshitori.visits";

/** The visits after opening the export generated at `generated`: a newer export moves the last one to `before`; the same one, or an
 * older one opened again, changes nothing; a record that cannot be read is a first visit. */
export function nextVisits(stored: Visits, generated: string): Visits {
  return stored.seen !== null && generated <= stored.seen ? stored : { seen: generated, before: stored.seen };
}

export function readVisits(): Visits {
  try {
    const raw = window.localStorage.getItem(VISITS);
    if (raw === null) return { seen: null, before: null };
    const p: unknown = JSON.parse(raw);
    if (typeof p !== "object" || p === null) return { seen: null, before: null };
    const { seen, before } = p as Record<string, unknown>;
    return {
      seen: typeof seen === "string" ? seen : null,
      before: typeof before === "string" ? before : null,
    };
  } catch {
    return { seen: null, before: null };
  }
}

/** Record that the export generated at `generated` was shown, and return what the person had seen before it. */
export function visit(generated: string): Visits {
  const next = nextVisits(readVisits(), generated);
  try {
    window.localStorage.setItem(VISITS, JSON.stringify(next));
  } catch {
    // storage refused: every visit is a first one
  }
  return next;
}

/** The alerts that happened after the export last seen before this one (RFC 3339 times of one width compare as text); none on a first visit. */
export function since(alerts: DecisionAlert[], before: string | null): DecisionAlert[] {
  return before === null ? [] : alerts.filter((a) => a.at > before);
}
