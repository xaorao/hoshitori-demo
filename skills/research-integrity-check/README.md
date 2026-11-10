# research-integrity-check

A read-only research skill. Given one market claim, it says what the claim is made of: its clauses with their exact words, the causal links, conditions, forecasts, numbers with units, assets and time words in it, and the structural problems visible from the words alone (a forecast with no horizon or no level, a number with no unit, a link that says one thing causes another).

It reads structure only. Nothing is checked against any source, so no part is called true or false, and no stance, probability or call is given. The only triage is `INVESTIGATE` (the claim names an asset or a number, so a source could test it) or `NOT_MARKET_TESTABLE`. Digits are read; numbers written in words are only flagged. No network, no model, no dependencies beyond Python 3.12.

## Run

```
uv venv --python 3.12 .venv && uv pip install -e ".[dev]" && source .venv/bin/activate    # or: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && source .venv/bin/activate
echo '{"claim":"Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."}' | research-integrity-check check
research-integrity-check example      # a working request
research-integrity-check tools        # the MCP tool definition
research-integrity-check serve-stdio  # MCP over standard input and output
research-integrity-check serve-http   # REST (POST /tools/research_integrity_check/call) and MCP over HTTP, on 127.0.0.1:8768 (--port to change)
```

The answer uses the RYO envelope. Every mark and clause carries `start` and `end` offsets and the exact text. A refused request names its field and carries a working example. A claim is data: words in it that look like instructions are read as words.

## Tests

With the environment active, `pytest` runs offline tests: planted claims for each structural point and for what must not trigger it, exact spans, refusals, the tool definition in the house style, and the MCP and REST routes.

## Licence

MIT (`LICENSE`). This skill is one of four standalone packages in the Hoshitori repository; its place in the product is described in the repository README.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx research-integrity-check mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/research-integrity-check -->
