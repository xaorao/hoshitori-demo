// The case files the tests replay (written by tests/vectors/make_*.py; gzip keeps the repository small).
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";

export const loadCases = (name) => JSON.parse(gunzipSync(readFileSync(fileURLToPath(new URL(`./vectors/${name}.json.gz`, import.meta.url)))).toString("utf8")).cases;
