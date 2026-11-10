import { test } from "node:test";
import assert from "node:assert/strict";
import { abbreviateCommand, commandOf, envelopeKeys, firstJson, headline, parseLog, planTerminal, resettleCount, SPEEDUPS } from "../src/logs.ts";

test("a recorded log is cleaned of colour, carriage returns and banners, and its exit marker is read", () => {
  const log = parseLog("Script started on x\r\n\u001b[32m$ run --ok\u001b[0m\r\nfine\r\nexit 3\r\nScript done\r\n");
  assert.deepEqual(log.lines, ["$ run --ok", "fine"]);
  assert.equal(log.exit, 3);
  assert.equal(commandOf(log), "run --ok");
});

test("commands are shown in the film's short form, every kept token being the log's own", () => {
  assert.equal(
    abbreviateCommand("python -m hoshitori.cli video/public/record/bundles/cy46wffyr33jfeukuin327cnicmw --trusted-root video/public/record/trusted_root.json --now 2026-11-10T11:30:17Z"),
    "hoshitori.cli <cycle> --trusted-root …",
  );
  assert.equal(abbreviateCommand("node verifier-js/bin/verify_bundle.mjs bundles/cyaaaaaaaaaaaaaaaaaaaaaaaa --trusted-root t.json"), "verify_bundle.mjs <cycle> --trusted-root …");
  assert.equal(abbreviateCommand("address-flag-check check --address 0x000000000000000000000000000000000000dEaD --chain ethereum"), "address-flag-check check --address 0x0000…dEaD --chain ethereum");
  assert.equal(abbreviateCommand("address-flag-check check --address 1BitcoinEaterAddressDontSendf59kuE --chain bitcoin"), "address-flag-check check --address 1Bitco…9kuE --chain bitcoin");
  assert.equal(abbreviateCommand("indicator-condition-check check --symbol SOL --condition '{\"left\":{}}'"), "indicator-condition-check check --symbol SOL --condition '{…}'");
});

test("the envelope is the first JSON document, found by brace depth and not by the first brace in a command", () => {
  const log = parseLog("$ x --condition '{\"a\":1}'\n{\n  \"schema_version\": \"1.0\",\n  \"summary\": {\"headline\": \"h { } \\\" }\", \"key_points\": [\"k\"]}\n}\n$ y\n{\n \"z\": 1\n}\n");
  assert.deepEqual(envelopeKeys(log), ["schema_version", "summary"]);
  assert.equal(headline(log), 'h { } " }');
  assert.equal(firstJson(parseLog("no json here")), null);
});

test("the re-settle count is the log's own last line, and a failed run has none", () => {
  const ok = parseLog("$ cmd\nre-settled from Binance: 568 match, 0 do not\nexit 0\n");
  assert.equal(resettleCount(ok)?.matched, "568");
  assert.equal(resettleCount(parseLog("$ cmd\nre-settled from Binance: 568 match\nexit 1\n")), null);
  assert.equal(resettleCount(parseLog("$ cmd\nTraceback\nexit 0\n")), null);
  assert.equal(resettleCount(parseLog("$ cmd\n412 of 412 settled up-or-down calls match\n")), null, "no exit code, no claim");
});

test("terminal plans: typed at 40 cps when it fits, outputs printed when not, commands faster only as a last resort", () => {
  const rows = [
    { kind: "cmd" as const, text: "x".repeat(40) },
    { kind: "result" as const, text: "y".repeat(80) },
  ];
  const roomy = planTerminal(rows, 10, 40, 30, 8, 9, 30);
  assert.deepEqual(roomy.typed, [true, true]);
  assert.equal(roomy.speed, 1);
  const tight = planTerminal(rows, 3, 40, 30, 8, 9, 30);
  assert.deepEqual(tight.typed, [true, false]);
  const tiny = planTerminal(rows, 1.7, 40, 30, 8, 9, 30);
  assert.ok(tiny.speed > 1 && tiny.speed <= (SPEEDUPS[SPEEDUPS.length - 1] ?? 1));
});
