# exit-liquidity-check

A read-only research skill for agents. For one token and an optional position size it answers:

> Can this position be closed, and what does closing it cost?

RYO's six research tools all answer the entry question: what is this token doing, is it worth entering. None says what it costs to get
out. This skill reads on-chain pool depth and, for Solana tokens, an executable Jupiter quote of the exact size, and returns the price
impact in percent and dollars, the largest exit at 1 percent, 3 percent and your own ceiling, and the hours a patient exit takes at a
share of daily volume. No key, account or sign-up is needed.

```text
BONK: exiting $50,000 costs 42.5% (quoted); the exit is severe at this size.
  Jupiter quoted $28,817 back for the sale: a cost of 42.5%, fees included.
  Modelled from pool depth: 8.59% ($4,297); the exit is 9.40% of the pool.
  Largest exit at 1%: $5,372; at 3%: $16,449; at your ceiling: $16,449.
  A patient exit at 10% of daily volume takes 23.4 hours.
```

That is real output from one call. The model and the quote disagree by a factor of five, and the answer shows both: the verdict rests on the
quote, and the model stays beside it so its error is visible.

## Modelled is not measured

* The pool figures feed a constant-product model (`cpmm_v1`). Its assumptions travel with every answer (`data.model`), and past 25 percent
  of the pool it says so and calls the cost a floor.
* Only a Jupiter quote is observed. Confidence is `high` for a quote of this exact size and never above `moderate` for a modelled figure.
* A figure that could not be read is `null` and named under `gaps`. It is never zero, and a dead source is never a liquidity rating.
* Pools are found by the token's canonical contract, never by symbol, and a pool is rejected, with its reason, when the token is its quote
  side, it has no liquidity or price, did not trade in 24 hours, its other side is not a major quote asset, or its price is more than
  3 percent from the volume-weighted median. A search by symbol finds impostor pools with hundreds of millions in reported liquidity.

## Install and first run (about 3 minutes)

You need Python 3.12 or later and a network. From the folder that holds this README (`skills/exit-liquidity-check`):

```bash
uv venv --python 3.12 .venv && uv pip install -e .          # or: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/exit-liquidity-check check --token SOL --size-usd 50000
```

The output is a JSON envelope with the RYO fields `schema_version`, `tool`, `status`, `data_mode`, `as_of`, `request`, `data`, `summary`,
`availability` and `warnings`. `summary.headline` is the sentence above; `data` holds every number with where it came from, and
`data.source_calls` holds the hash of every upstream reply.

Other commands: `sources` (what is covered, sends nothing), `tool` (the tool definitions), `example`, `mcp` (MCP over stdio) and `serve`
(REST and MCP over HTTP on 127.0.0.1:8767: `POST /tools/exit_liquidity_check/call`, `GET /tools`, `GET /health`).

From Python: `from exit_liquidity_check import run; run({"token": "SOL", "size_usd": 50000})`.

## Arguments

| Argument | Meaning |
|---|---|
| `token` | One of SOL, BONK, JUP, WIF, ETH, BTC. ETH and BTC are measured through WETH and WBTC on Ethereum. |
| `size_usd` | The position size in US dollars. Omit it for the ceilings and the cost curve without a verdict. |
| `max_slippage_pct` | The price impact you accept (default 3). The verdict is `ok` up to it, `tight` up to twice it, `illiquid` above. |
| `participation_pct` | The share of 24 hour volume you will be while exiting patiently (default 10). |

Anything else is refused before a request is sent, with the field named and a working example. A token that is not covered is refused with
the list of covered tokens.

## Sources and limits

* DexScreener `token-pairs` (pool liquidity, 24 hour volume, price) and the Jupiter `lite-api` quote. Both are keyless public services; they
  have rate limits, which the skill respects with retries, `Retry-After` and a cool-down.
* On-chain depth only. Centralised-exchange depth and volume are not included, so for a token with large exchange trading the time to exit
  reads long and the cost reads high.
* Six tokens. Adding one means adding its canonical contract to `registry.py` and running `pytest -m live`, which checks that the contract is
  the base token of live pools.
* No quote exists for ETH and BTC: Jupiter is a Solana router, and no keyless executable-quote venue was found for their home chain. Their
  answers are modelled and say so.
* The model has been compared with a venue quote in the calls above, not in a study over many tokens and sizes. That study has not been run.

## Tests

```bash
uv pip install -c constraints.txt -e ".[dev]"
.venv/bin/pytest                   # offline: hand-computed model values, properties, recorded replies, hostile replies, failures, refusals
.venv/bin/pytest -m live           # calls DexScreener and Jupiter once per token
.venv/bin/ruff check . && .venv/bin/mypy
```

The recorded replies in `tests/fixtures/` are unmodified public responses and are labelled recorded.

## Credit

The model, the verdict bands, the confidence rule and the idea of a registry of canonical contracts follow `exit_liquidity` in
`github.com/IamHarrie-Labs/hanko` (MIT, Copyright (c) 2026 Harrie). The code here is written for this package: standard library only, its own
transport, parsers, refusals, tests and envelope. See `NOTICE.md` in the repository root.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx exit-liquidity-check mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/exit-liquidity-check -->
