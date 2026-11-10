# ryo-verdict-record

A read-only research skill for agents. Given the outcomes of RYO's own verdicts in a published record, it answers:

> Have RYO's verdicts been right, and how sure can anyone be yet?

RYO's `deep_analysis` returns a verdict word and a preview plan for a token. Nothing in RYO's tool set says how those verdicts settled. A
Hoshitori record keeps each verdict (read as up, down or against buying), time-stamped before the next 24 hours and settled on exchange
candles by fixed rules. This skill reads those outcomes and reports the result honestly: what could be scored, why the rest could not, the
hit rate of each reading with an interval, and the market's own rate to set it against.

```text
RYO's verdicts: 13 of 15 read could be scored; UP 3/9 (not distinguishable); NOT_LONG 2/4 (insufficient).
```

(Illustrative wording of the output; the record in this repository holds no RYO read yet, and the skill says so rather than inventing one:
"No RYO verdict is in this record, so there is nothing to score. That is not a finding about RYO.")

## The rules

* A reading is **right** when it said up and the window rose, or said down or against buying and the window fell.
* A tie, a window not yet settled and a reading that is not a direction are **not scored**. They are counted with their reason and are never a
  miss.
* The hit rate is hits over scored reads, with a 95 percent Wilson interval. It is set against the **market's own rate** for that side, from
  every scored window in the record: the share that rose for up, the share that fell for down and against buying.
* Before `min_scored` scored reads of a reading (default 10) there is no comparison: `insufficient`. After it, `above_market` or
  `below_market` only when the whole interval is on that side of the market's rate; otherwise `not_distinguishable`.
* The recorded `hit` is checked against the rule. A disagreement is reported and the rule's hit is used.

`ryo-verdict-record rules` prints these. The plan counts say which of RYO's own levels came first (target, stop, timeout, ambiguous) or why
there is no answer.

## Install and first run (about 2 minutes)

You need Python 3.12 or later. From the folder that holds this README (`skills/ryo-verdict-record`):

```bash
uv venv --python 3.12 .venv && uv pip install -e .          # or: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/ryo-verdict-record example > example.json          # a working call: {"record": {...}, "min_scored": 2}
.venv/bin/python -c "import json; json.dump(json.load(open('example.json'))['record'], open('record.json','w'))"
.venv/bin/ryo-verdict-record score record.json --min-scored 2
```

A record is the `outcomes` list of a Hoshitori site's `ryo.json` (an object holding it, or the list itself). Each outcome needs `symbol`,
`reading` (UP, DOWN, NOT_LONG, NONE or UNREAD), `direction` (ROSE, FELL, TIE or null), and may carry `state`, `hit` and `plan`.

From Python: `from ryo_verdict_record import run; run({"record": record})`. Over MCP (stdio) with `ryo-verdict-record mcp`, or over HTTP with
`ryo-verdict-record serve` (`POST /tools/ryo_verdict_record/call`, `GET /tools`, `/api/skills/`).

## Arguments

| Argument | Meaning |
|---|---|
| `record` | The outcomes, as above. Required. At most 5,000. |
| `symbol` | Read only the verdicts on this token (capital letters and digits). |
| `min_scored` | The scored reads a reading needs before it is compared with the market (1 to 1,000; default 10). |

Bad input is refused before anything is scored, with the field named and a working example. An empty record is `unavailable`, never a
finding.

## Limits

It reads only what it is given and sends nothing. The rates describe the settled windows of the record, not the future. One venue pair
settled the windows; a few dozen reads cannot separate a good reading from luck, which is what `insufficient` and `not_distinguishable` say.

## Tests

```bash
uv pip install -c constraints.txt -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/mypy
```

Hand-computed cases for every outcome and every refusal, the Wilson interval against known values, properties on random records (the
counts add up, the order of a record does not matter, intervals contain the rate), and the MCP, HTTP and command-line surfaces.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx ryo-verdict-record mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/ryo-verdict-record -->
