import assert from "node:assert/strict";
import { test } from "node:test";
import { JUDGE_MAP, TRACKS } from "../src/judges.ts";
import { WHERE_WORDS } from "../src/format.ts";
import { href, parse } from "../src/route.ts";

// The rubric as the organiser publishes it: per entered track 40 common points plus 60 track points.
const RUBRIC: Record<string, [string, number][]> = {
  common: [["Combining sources", 20], ["Coping with failure", 20]],
  agents: [["Real thinking", 25], ["Cause and effect", 20], ["Repeatability", 10], ["Advanced execution", 5]],
  interfaces: [["Easy to read", 25], ["Sorted by importance", 25], ["Works for everyone", 10]],
  skills: [["Fills a gap", 25], ["Follows the spec", 15], ["Could we use it", 20]],
};

test("every rubric line is mapped once, with the published weight, and nothing else is", () => {
  assert.deepEqual(Object.keys(RUBRIC).sort(), Object.keys(TRACKS).sort());
  for (const [group, lines] of Object.entries(RUBRIC)) {
    const mapped = JUDGE_MAP.filter((r) => r.group === group).map((r) => [r.line, r.points] as [string, number]);
    assert.deepEqual(mapped, lines, group);
  }
  assert.equal(JUDGE_MAP.length, 12);
});

test("the points add up to the published 40 and 60", () => {
  const sum = (group: string): number => JUDGE_MAP.filter((r) => r.group === group).reduce((n, r) => n + r.points, 0);
  assert.equal(sum("common"), 40);
  for (const g of ["agents", "interfaces", "skills"]) assert.equal(sum(g), 60, g);
});

test("every line names a real page, says what to look at, and says what it does not show", () => {
  for (const r of JUDGE_MAP) {
    assert.notEqual(parse(href(r.where)).page, "missing", r.line);
    assert.equal(href(parse(href(r.where))), href(r.where), r.line);
    assert.ok(r.look.length > 20 && r.look === r.look.trim(), `${r.line}: look`);
    assert.ok(r.limit.length > 20 && r.limit === r.limit.trim(), `${r.line}: limit`);
  }
});

test("the words claim no score and no win", () => {
  for (const r of JUDGE_MAP) assert.doesNotMatch(`${r.look} ${r.limit}`, /\b(wins?|best|perfect|guarantee[sd]?|full marks|will score)\b/i, r.line);
});

test("every page a line points to has its words for the link", () => {
  for (const r of JUDGE_MAP) assert.ok(WHERE_WORDS[r.where.page] !== undefined, r.where.page);
});
