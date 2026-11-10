# MCP Registry `server.json` schema (recorded copy)

`server.schema.json` is the official MCP Registry schema that the registry documentation points every `server.json` at (`$schema` in the
examples of the specification repository's `docs/registry/*.mdx`). It is a third-party file, kept unchanged so the skill's tests can validate
`server.json` offline.

- Source: the address in the file's own `$id` (JSON Schema draft-07; generated from
  `docs/reference/api/openapi.yaml` of `github.com/modelcontextprotocol/registry`, as its `$comment` says).
- How it was fetched: once, with `curl -sS <source> -o server.schema.json`; public, no account, no key; HTTP 200, 22090 bytes.
- SHA-256: `3fba09590c99f61735d234822279f4223fab9e300c0a81e81c91ab62a4114de0` (checked by `tests/test_registry_manifest.py`).
- Licence: the `modelcontextprotocol/registry` repository's `LICENSE` (read at
  `https://raw.githubusercontent.com/modelcontextprotocol/registry/main/LICENSE`): MIT, moving to Apache-2.0 for new contributions;
  documentation CC-BY-4.0.
- To refresh: fetch the same URL again, replace the file, update the hash in the test, and re-run the manifest test. A schema with a new `$id`
  means a new `$schema` URL in `server.json` as well.
