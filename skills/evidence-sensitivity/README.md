# evidence-sensitivity

A read-only research skill. Given a rule-based call and the evidence it was made from, it shows which pieces of evidence the call rests on: what the call becomes with each item withheld or neutralised, what happens if a whole source group is removed, how far each signal would have to move to change the call, and the smallest set of items that alone gives the same call.

It measures how a rule responds to its inputs. It does not say whether the evidence is true, why a model decided, or what the market will do. Nothing is fetched, so the answer's `data_mode` is `unknown`.

## The rule it explains

The weighted mean of the available signals (each from -1 to 1). LONG when the mean is at least the threshold, SHORT when it is at most minus the threshold, otherwise NO_CALL. Fewer available items than `min_evidence` is NO_CALL. A signal of `null` means the item was unavailable: it counts for nothing and is listed, with whether a value it might have had could have changed the call. Arithmetic is exact.

## Install and run

```
uv venv --python 3.12 .venv && uv pip install -e ".[dev]" && source .venv/bin/activate    # or: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && source .venv/bin/activate
echo '{"evidence":[{"id":"momentum_7d","signal":0.6,"weight":2,"group":"market"},{"id":"funding","signal":-0.2,"group":"derivatives"}],"rule":{"threshold":0.25}}' | evidence-sensitivity check
evidence-sensitivity example        # a working request
evidence-sensitivity tools          # the MCP tool definition
evidence-sensitivity serve-stdio    # MCP over standard input and output
evidence-sensitivity serve-http     # REST (POST /tools/evidence_sensitivity/call) and MCP over HTTP, on 127.0.0.1:8767 (--port to change)
```

No dependencies beyond Python 3.12. The answer uses the RYO envelope (`schema_version`, `tool`, `status`, `data_mode`, `as_of`, `request`, `data`, `summary`, `availability`, `warnings`); a refused request names its field and carries a working example.

## Reading an answer

- `data.baseline`: the score, the call, how many items were available.
- `data.items[].reading`: `REQUIRED` (withholding it changes the call), `SUPPORTING`, `OPPOSING`, `NEUTRAL`, `MISSING` (and `LEANS_UP` / `LEANS_DOWN` when the call is NO_CALL).
- `data.items[].flip`: the signal value past which the call changes, and the distance from the current signal; null when no value in range could change it.
- `data.groups[]`: the call with a whole origin family removed. Correlated sources count as one.
- `data.sufficient_set`: the smallest set of items that alone gives the same call (every subset tried up to 16 available items; greedy beyond, and it says so).

## Tests

With the environment active, `pytest` runs 68 offline tests: a hand calculation, 40 random cases checked against a second independent implementation of the rule, the boundaries of every flip, every refusal, the MCP and REST routes, and the envelope against its schema.

## Licence

MIT (`LICENSE`). This skill is one of four standalone packages in the Hoshitori repository; its place in the product is described in the repository README.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx evidence-sensitivity mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/evidence-sensitivity -->
