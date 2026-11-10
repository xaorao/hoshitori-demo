import assert from "node:assert/strict";
import { test } from "node:test";
import { href, parse } from "../src/route.ts";

const ID = "fc4opxtnhk3fprz3b4wufs2aclcx";

test("the pages", () => {
  assert.deepEqual(parse(""), { page: "today" });
  assert.deepEqual(parse("#"), { page: "today" });
  assert.deepEqual(parse("#/"), { page: "today" });
  assert.deepEqual(parse("#/record"), { page: "record" });
  assert.deepEqual(parse("#/verify"), { page: "verify" });
  assert.deepEqual(parse("#/quality"), { page: "quality" });
  assert.deepEqual(parse("#/drill"), { page: "drill" });
  assert.deepEqual(parse("#/brand"), { page: "brand" });
  assert.deepEqual(parse("#/claims"), { page: "claims" });
  assert.deepEqual(parse("#/challenge"), { page: "challenge" });
  assert.deepEqual(parse("#/judges"), { page: "judges" });
  assert.deepEqual(parse("#/practice"), { page: "practice" });
  assert.deepEqual(parse("#/practice/"), { page: "missing", path: "/practice/" });
  assert.deepEqual(parse("#/lab"), { page: "lab" });
  assert.deepEqual(parse("#/lab/"), { page: "missing", path: "/lab/" });
  assert.deepEqual(parse(`#/receipts/${ID}`), { page: "receipt", id: ID });
});

test("the calls of a cycle", () => {
  const CY = "cyhvcgqshhqwpxphtbjtjygvkw63";
  assert.deepEqual(parse("#/calls"), { page: "calls", cycle: null });
  assert.deepEqual(parse(`#/calls/${CY}`), { page: "calls", cycle: CY });
  assert.deepEqual(parse("#/calls/"), { page: "missing", path: "/calls/" });
  assert.deepEqual(parse("#/calls/../index"), { page: "missing", path: "/calls/../index" });
  assert.deepEqual(parse(`#/calls/${ID}`), { page: "missing", path: `/calls/${ID}` });
  for (const hash of ["#/calls", `#/calls/${CY}`]) assert.equal(href(parse(hash)), hash);
});

test("an address that is not a page is missing, and a receipt id is not a path", () => {
  assert.deepEqual(parse("#/nope"), { page: "missing", path: "/nope" });
  assert.deepEqual(parse("#/receipts/"), { page: "missing", path: "/receipts/" });
  assert.deepEqual(parse("#/receipts/../index"), { page: "missing", path: "/receipts/../index" });
  assert.deepEqual(parse(`#/receipts/${ID}/x`), { page: "missing", path: `/receipts/${ID}/x` });
  assert.deepEqual(parse("#/receipts/ft4opxtnhk3fprz3b4wufs2aclcx"), { page: "missing", path: "/receipts/ft4opxtnhk3fprz3b4wufs2aclcx" });
});

test("an address and its page agree", () => {
  for (const hash of ["#/", "#/record", "#/verify", "#/quality", "#/drill", "#/brand", "#/claims", "#/challenge", "#/desk", "#/practice", "#/lab", "#/judges", `#/receipts/${ID}`]) assert.equal(href(parse(hash)), hash);
  assert.equal(href({ page: "missing", path: "/x" }), "#/");
});

test("one Desk run or one practice book can be opened by its address", () => {
  assert.deepEqual(parse("#/desk/drunabc123"), { page: "desk", run: "drunabc123" });
  assert.deepEqual(parse("#/practice/L1:BASE"), { page: "practice", book: "L1:BASE" });
  assert.equal(href({ page: "desk", run: "drunabc123" }), "#/desk/drunabc123");
  assert.equal(href({ page: "practice", book: "L0.shadow:STRESS" }), "#/practice/L0.shadow:STRESS");
  for (const hash of ["#/desk/od-run-1a", "#/practice/L1x:BASE"]) assert.equal(href(parse(hash)), hash);
  assert.deepEqual(parse("#/desk/../x"), { page: "missing", path: "/desk/../x" });
  assert.deepEqual(parse("#/desk/a b"), { page: "missing", path: "/desk/a b" });
  assert.deepEqual(parse("#/desk/-x"), { page: "missing", path: "/desk/-x" });
  assert.deepEqual(parse(`#/desk/${"a".repeat(129)}`), { page: "missing", path: `/desk/${"a".repeat(129)}` });
});

test("every call on one token, or of one agent, has an address", () => {
  assert.deepEqual(parse("#/calls/token/BTC"), { page: "callsby", by: "token", value: "BTC" });
  assert.deepEqual(parse("#/calls/agent/L1x"), { page: "callsby", by: "agent", value: "L1x" });
  assert.equal(href({ page: "callsby", by: "token", value: "ETH" }), "#/calls/token/ETH");
  for (const hash of ["#/calls/token/1INCH", "#/calls/agent/L0"]) assert.equal(href(parse(hash)), hash);
  assert.deepEqual(parse("#/calls/token/"), { page: "missing", path: "/calls/token/" });
  assert.deepEqual(parse("#/calls/other/BTC"), { page: "missing", path: "/calls/other/BTC" });
  assert.deepEqual(parse("#/calls/token/B-TC"), { page: "missing", path: "/calls/token/B-TC" });
});
