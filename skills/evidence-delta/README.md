# evidence-delta

A read-only research skill for agents. Given two reads of the same tool, it answers:

> What is different between them, field by field, and what could not be compared?

RYO's tools answer for one moment. An agent that calls `analyze_token` an hour later has two answers and no tool that says what moved. Doing
it by eye or with a generic diff goes wrong in a specific way: a field that is `null` or missing in the second read looks like a change, or
worse, like "nothing changed". This skill keeps three outcomes apart and never infers one from another.

```text
analyze_token: 1 of 3 fields changed over 60 minutes: price_usd +4.00%.
  1 changed, 1 unchanged, 1 could not be compared.
  Not compared: rsi_14.            (known before, null after: neither changed nor unchanged)
```

## The rules

* **changed**: both reads have a value there and the values differ. A number reports its relative move; `min_change_pct` lets you treat small
  moves as unchanged.
* **unchanged**: both reads have a value there and they are equal.
* **unknown**: a read has no value there. The reason is `null`, `absent` (no such field) or `unusable_number`. Neither of the other two is
  inferred from it.
* The verdict is `changed` if anything changed; `unchanged` only if every compared field is known in both reads; `unchanged_where_known`
  if nothing known changed but some fields could not be compared; `insufficient_evidence` if no field is known in both.

`evidence-delta rules` prints these and the limits. Strings are compared exactly, booleans never equal numbers, and a list is compared index
by index (an inserted element shifts the ones after it). It reads nothing from the network and does not say why a field changed or whether
the change matters.

## Install and first run (about 2 minutes)

You need Python 3.12 or later. From the folder that holds this README (`skills/evidence-delta`):

```bash
uv venv --python 3.12 .venv && uv pip install -e .          # or: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/evidence-delta example > example.json              # a working call: {"before": ..., "after": ...}
.venv/bin/python -c "import json; d=json.load(open('example.json')); json.dump(d['before'], open('before.json','w')); json.dump(d['after'], open('after.json','w'))"
.venv/bin/evidence-delta compare before.json after.json
```

A read is either an RYO envelope (`tool`, `as_of`, `status`, `data_mode` and `data` are used) or just a data object. Two reads of different
tools are refused. A `partial` or `simulated` read is warned about, since its missing fields are unknown, not unchanged.

From Python: `from evidence_delta import run; run({"before": b, "after": a})`. Over MCP (stdio) with `evidence-delta mcp`, or over HTTP with
`evidence-delta serve` (`POST /tools/evidence_delta/call`, `GET /tools`, `GET /health`).

## Arguments

| Argument | Meaning |
|---|---|
| `before`, `after` | The two reads (envelopes or data objects). Required. |
| `fields` | Dotted paths to compare (`a.b.0.c`). A path neither read holds is reported as unknown, never dropped. Default: every field. |
| `min_change_pct` | A number whose move is below this percent counts as unchanged (default 0). |

Limits: depth 12, 2,000 fields per read, 200 entries shown per list (the counts are always complete and `truncated` says when a list is cut).
Bad input is refused before anything is compared, with the field named and a working example.

## Tests

```bash
uv pip install -c constraints.txt -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/mypy
```

The suite holds hand-built cases for every outcome, properties on random documents (swapping the reads keeps the counts and the paths, a
read against a copy of itself never changes, every unknown carries its reason, no path is in two lists), hostile input, and the MCP and
command-line surfaces.

## Credit

The idea of comparing two reads of one RYO tool, keeping nulls and gaps out of "unchanged", comes from the `evidence_delta` skill of a public
entry in this hackathon (`Victork19/scarbook`, no licence; ideas only, nothing of its code or wording was used). The code here is written
from the rules above.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx evidence-delta mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/evidence-delta -->
