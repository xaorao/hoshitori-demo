import assert from "node:assert/strict";
import { test } from "node:test";
import { FORMAT, LoadError, checked, sha256Hex } from "../src/load.ts";
import type { SiteIndex } from "../src/types.ts";

const enc = new TextEncoder();
const bytes = (text: string): Uint8Array<ArrayBuffer> => new Uint8Array(enc.encode(text));

async function indexFor(files: Record<string, string>): Promise<SiteIndex> {
  const digests: Record<string, string> = {};
  for (const [path, text] of Object.entries(files)) digests[path] = await sha256Hex(bytes(text));
  return { format: FORMAT, generated_at: "2026-10-30T00:00:00.000Z", mode: "Exploratory run: not a scored period", counts: { cycles: 0, rows: 0, receipts: 0 }, files: digests };
}

test("the digest is SHA-256 in lower-case hex", async () => {
  assert.equal(await sha256Hex(bytes("")), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  assert.equal(await sha256Hex(bytes("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

test("a document the index describes is parsed", async () => {
  const index = await indexFor({ "today.json": '{"a":1}\n' });
  assert.deepEqual(await checked(index, "today.json", bytes('{"a":1}\n')), { a: 1 });
});

test("a document that is not the one the index describes is refused", async () => {
  const index = await indexFor({ "today.json": '{"a":1}\n' });
  await assert.rejects(checked(index, "today.json", bytes('{"a":2}\n')), (e: unknown) => e instanceof LoadError && e.kind === "changed" && e.path === "today.json");
});

test("a document the index does not know is refused", async () => {
  const index = await indexFor({ "today.json": "{}\n" });
  await assert.rejects(checked(index, "rows.json", bytes("{}\n")), (e: unknown) => e instanceof LoadError && e.kind === "missing");
});

test("a document with the right digest that is not JSON is unreadable, not shown", async () => {
  const index = await indexFor({ "today.json": "not json" });
  await assert.rejects(checked(index, "today.json", bytes("not json")), (e: unknown) => e instanceof LoadError && e.kind === "unreadable");
  const broken = new Uint8Array([0xff, 0xfe, 0xfd]);
  const bad: SiteIndex = { ...index, files: { "today.json": await sha256Hex(broken) } };
  await assert.rejects(checked(bad, "today.json", broken), (e: unknown) => e instanceof LoadError && e.kind === "unreadable");
});

test("an index whose time the pages cannot read is refused, so it cannot take every page down", async () => {
  const { loadIndex } = await import("../src/load.ts");
  const real = globalThis.fetch;
  const serve = (generated_at: unknown): void => {
    const body = JSON.stringify({ format: FORMAT, generated_at, mode: "Exploratory run: not a scored period", counts: { cycles: 0, rows: 0, receipts: 0 }, files: {} });
    globalThis.fetch = async () => new Response(body, { status: 200 });
  };
  try {
    serve("2026-10-30T22:40:24.966Z");
    assert.equal((await loadIndex("./data")).generated_at, "2026-10-30T22:40:24.966Z");
    for (const bad of ["unknown", "yesterday", "2026-13-01T00:00:00Z", null, 7]) {
      serve(bad);
      await assert.rejects(loadIndex("./data"), (e: unknown) => e instanceof LoadError && e.kind === "unreadable" && e.path === "index.json", String(bad));
    }
  } finally {
    globalThis.fetch = real;
  }
});

test("the state of the ledger an index states is read, and one the pages cannot read is refused", async () => {
  const { loadIndex } = await import("../src/load.ts");
  const real = globalThis.fetch;
  const serve = (ledger: unknown): void => {
    const body = JSON.stringify({ format: FORMAT, generated_at: "2026-10-30T22:40:24.966Z", ledger, mode: "Exploratory run: not a scored period", counts: { cycles: 0, rows: 0, receipts: 0 }, files: {} });
    globalThis.fetch = async () => new Response(body, { status: 200 });
  };
  try {
    serve({ state: "INTEGRITY_FAILURE", detail: "segment segment-000000000000.jsonl: leaf hash does not match" });
    assert.deepEqual((await loadIndex("./data")).ledger, { state: "INTEGRITY_FAILURE", detail: "segment segment-000000000000.jsonl: leaf hash does not match" });
    serve({ state: "OK", detail: "" });
    assert.deepEqual((await loadIndex("./data")).ledger, { state: "OK", detail: "" });
    for (const bad of ["INTEGRITY_FAILURE", null, 7, { state: 7, detail: "" }, { state: "OK" }, { detail: "" }, { state: "", detail: "" }]) {
      serve(bad);
      await assert.rejects(loadIndex("./data"), (e: unknown) => e instanceof LoadError && e.kind === "unreadable" && e.path === "index.json", JSON.stringify(bad));
    }
  } finally {
    globalThis.fetch = real;
  }
});

test("an export of another format is refused with a sentence that names both formats", async () => {
  const { loadIndex, FORMAT } = await import("../src/load.ts");
  const real = globalThis.fetch;
  const serve = (format: unknown): void => {
    const body = JSON.stringify({ format, generated_at: "2026-10-30T22:40:24.966Z", mode: "Exploratory run: not a scored period", counts: { cycles: 0, rows: 0, receipts: 0 }, files: {} });
    globalThis.fetch = async () => new Response(body, { status: 200 });
  };
  try {
    serve(FORMAT);
    assert.equal((await loadIndex("./data")).format, FORMAT);
    for (const [bad, said] of [
      [FORMAT + 1, `exported in format ${FORMAT + 1}`],
      [0, "exported in format 0"],
      ["1", 'states the format "1"'],
      [null, "states no format"],
      [1.5, "states the format 1.5"],
    ] as const) {
      serve(bad);
      await assert.rejects(
        loadIndex("./data"),
        (e: unknown) => e instanceof LoadError && e.kind === "unreadable" && e.path === "index.json" && e.message.includes(said) && e.message.includes(`reads format ${FORMAT}`) && e.message.includes("Export it again"),
        String(bad),
      );
    }
  } finally {
    globalThis.fetch = real;
  }
});

test("an index that is not an object is refused with the same sentence, and one that is not JSON with the general one", async () => {
  const { loadIndex } = await import("../src/load.ts");
  const real = globalThis.fetch;
  try {
    for (const body of ["null", "5", "[]", '"text"']) {
      globalThis.fetch = async () => new Response(body, { status: 200 });
      await assert.rejects(loadIndex("./data"), (e: unknown) => e instanceof LoadError && e.message.includes("states no format") && e.message.includes("Export it again"), body);
    }
    globalThis.fetch = async () => new Response("{not json", { status: 200 });
    await assert.rejects(loadIndex("./data"), (e: unknown) => e instanceof LoadError && e.message === "index.json is not a site index this page understands");
  } finally {
    globalThis.fetch = real;
  }
});
