import assert from "node:assert/strict";
import { test } from "node:test";
import { GROUPS } from "../src/nav.ts";
import { href, parse } from "../src/route.ts";

const all = GROUPS.flatMap((g) => g.items);

test("the groups, by what a person is doing, in this order", () => {
  assert.deepEqual(
    GROUPS.map((g) => g.name),
    ["Discover", "Investigate", "Results and verify", "For reviewers"],
  );
  assert.deepEqual(
    GROUPS.map((g) => g.items.map((i) => i.label)),
    [
      ["Today", "Calls", "Feed", "Voices and whales"],
      ["Check a claim", "The Desk", "Test a source", "Laboratory"],
      ["Track record", "Check a record", "Practice trades"],
      ["For judges", "Evidence of quality", "Failure drills"],
    ],
  );
});

test("every page of the bar is in the groups exactly once, and no label twice", () => {
  assert.equal(all.length, 14);
  assert.equal(new Set(all.map((i) => i.label)).size, 14);
  assert.equal(new Set(all.map((i) => i.route.page)).size, 14);
});

test("the four primary links, the ones a phone shows, are the journey: arrive, look, investigate, see the result", () => {
  assert.deepEqual(
    all.filter((i) => i.primary === true).map((i) => i.label),
    ["Today", "Calls", "Check a claim", "Track record"],
  );
});

test("every link keeps its address: the route of each reads back as the page it names", () => {
  for (const item of all) {
    assert.equal(parse(href(item.route)).page, item.route.page, item.label);
  }
  assert.deepEqual(
    all.map((i) => href(i.route)),
    [
      "#/",
      "#/calls",
      "#/feed",
      "#/voices",
      "#/claims",
      "#/desk",
      "#/challenge",
      "#/lab",
      "#/record",
      "#/verify",
      "#/practice",
      "#/judges",
      "#/quality",
      "#/drill",
    ],
  );
});
