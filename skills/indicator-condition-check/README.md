# indicator_condition_check

A read-only research skill for RYO-style agents. It answers one question honestly:

> Is this technical condition true **right now**, on the last **closed** candle, for this token? How far is each part of it from firing, how long ago did it last hold, and how often did it hold recently?

```bash
indicator-condition-check check --symbol SOL \
  --condition '{"all":[{"left":{"indicator":"rsi","period":14},"op":"<","right":{"value":30}},
                       {"left":{"indicator":"close"},"op":"cross_above","right":{"indicator":"ema","period":50}}]}'
```

## Install and first run (about 2 minutes)

**You need** Python 3.11 or later (`python3 --version`; if it is older, the `uv` line below fetches Python for you, from https://docs.astral.sh/uv/).
**Network:** installing downloads only the build tool; the skill has no run-time dependency. Every check reads public exchange candles, so
it needs the network and no key.

Run these from the folder that holds this README (`skills/indicator-condition-check`):

```bash
uv venv --python 3.11 .venv && uv pip install -e .      # or, with Python 3.11+ as python3: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/indicator-condition-check check --symbol SOL --condition '{"left":{"indicator":"rsi"},"op":"<","right":{"value":30}}'
```

**What success looks like.** A JSON envelope with `"status": "ok"`, `"data_mode": "live"` and a `summary.headline` such as
`SOL 1d: condition is TRUE on the last closed candle (2026-11-02T00:00:00Z, binance)` (or FALSE: the answer depends on the market).
`indicator-condition-check example` prints a longer working call. To keep a rule in a file, put the condition (the JSON after
`--condition`) in `rule.json` and pass `--condition @rule.json`; a file that is missing or is not text is refused with exit code 2 and a
message that names it, never a traceback.

## Why it exists

RYO's published tools expose four technical readings: `rsi_14`, `atr_14_pct`, `momentum_30d_pct` and `trend`. A user (or an agent) who wants to test *their own* rule, a moving-average cross, a MACD turn, a Bollinger squeeze, a volume spike, a breakout above the last 20 highs, has no tool for it, and no tool lets an agent turn a trade idea into a monitorable trigger or an explicit invalidation level. This skill is that tool. The organisers name it as an example ("Tech Indicator: run a user-defined condition against live market data").

## What you get back

The response uses the same envelope as RYO's builder tools: `schema_version`, `tool`, `status` (`ok`, `partial`, `unavailable`), `data_mode`, `as_of`, `request`, `data`, `summary`, `availability`, `warnings`.

Real output, replayed offline from the recorded Binance SOL/USDT daily response captured on 2026-10-20 at 11:34 UTC (condition: `rsi(14) < 70 AND close > ema(50)`, `lookback_bars` 250). Because the candles were injected rather than fetched, `data_mode` is `unknown` and a warning says so; a normal call reports `live`. The body hash is shortened and `methods` is omitted for space.

```json
{
  "status": "ok",
  "data_mode": "unknown",
  "as_of": "2026-10-20T11:34:15Z",
  "data": {
    "pair": {
      "resolved": "SOLUSDT",
      "venue_id": "SOLUSDT",
      "unified": "SOL/USDT",
      "base": "SOL",
      "quote": "USDT",
      "venue_used": "binance",
      "alternatives_tried": [
        {
          "venue": "binance",
          "outcome": "ok",
          "closed_bars": 299
        }
      ],
      "quote_note": "Quoted in USDT; USD venues can differ by a small basis."
    },
    "candles": {
      "timeframe": "1d",
      "count": 299,
      "first_ts": "2025-12-25T00:00:00Z",
      "last_closed_ts": "2026-10-19T00:00:00Z",
      "last_close": 105.08,
      "forming_bar_excluded": true,
      "gaps": [],
      "gap_count": 0
    },
    "result": {
      "satisfied": false,
      "clauses": [
        {
          "id": "c1",
          "expr": "rsi(14) < 70",
          "left": 41.1110309591173,
          "right": 70.0,
          "satisfied": true,
          "null_reason": null,
          "bars_since_last_true": 0,
          "last_true_ts": "2026-10-19T00:00:00Z",
          "true_count": 237,
          "defined_bars": 250
        },
        {
          "id": "c2",
          "expr": "close > ema(50)",
          "left": 105.08,
          "right": 106.8160018060697,
          "satisfied": false,
          "null_reason": null,
          "bars_since_last_true": 8,
          "last_true_ts": "2026-10-11T00:00:00Z",
          "true_count": 92,
          "defined_bars": 250
        }
      ]
    },
    "history": {
      "window_bars": 250,
      "defined_bars": 250,
      "times_true": 79,
      "last_true_ts": "2026-10-11T00:00:00Z",
      "bars_since_last_true": 8
    },
    "warmup": {
      "available": 299,
      "required_minimum": 50,
      "stable_for_all_clauses": true,
      "per_clause": [
        {
          "id": "c1",
          "expr": "rsi(14) < 70",
          "needed_stable": 99,
          "used": 299,
          "stable": true
        },
        {
          "id": "c2",
          "expr": "close > ema(50)",
          "needed_stable": 200,
          "used": 299,
          "stable": true
        }
      ]
    },
    "source_calls": [
      {
        "venue": "binance",
        "url": "https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1d&limit=451",
        "attempt": 1,
        "http_status": 200,
        "latency_ms": 0,
        "bytes": 53310,
        "sha256": "515a6e3cfaca3041...",
        "retry_after_s": null,
        "fetched_at": "2026-10-20T11:34:15Z",
        "server_date": null,
        "error_kind": null
      }
    ]
  },
  "summary": {
    "headline": "SOL 1d: condition is FALSE on the last closed candle (2026-10-19T00:00:00Z, binance)"
  },
  "availability": {
    "ohlc": "available",
    "condition": "complete",
    "warmup": "complete"
  },
  "warnings": [
    "Candles came from an injected data source (recorded or test data), not a live fetch in this call; data_mode is therefore unknown.",
    "The forming candle was excluded; the verdict is for the last closed candle."
  ]
}
```

## Honesty rules (each one has a test)

| Rule | Behaviour |
|---|---|
| Null is not false | A clause that cannot be decided (history too short, flat series) is `null` with a `null_reason`. `all`/`any`/`not` use three-valued logic; a certain `false` still wins inside `all`. |
| No NaN, no zero-filling | Undefined values are `null`; the payload is strict JSON. RSI on a series that never moved is undefined, not 50. |
| Closed candles only | The forming candle is dropped and `forming_bar_excluded` says so. |
| Gaps are never papered over | Missing candles are listed in `data.candles.gaps`; indicators are computed only on the contiguous candles after the last gap, and status becomes `partial`. |
| Unconverged indicators are flagged | EMA, RSI, ATR and MACD depend on how much history seeds them. `data.warmup` lists the bars each clause needs for a stable value (EMA: warm-up + 3n; Wilder RSI/ATR: warm-up + 6n; MACD: 4 x slow, + 3 x signal for the signal line); too few bars gives `partial` and a warning. |
| Upstream text is untrusted | Venue response bodies are never copied into the envelope. Errors use fixed wording per failure kind plus `http_status`, `retryable`, `retry_after_s`; a regression test feeds prompt-injection text through every venue. |
| Provenance per call | `data.source_calls` lists every upstream request: venue, URL, attempt, status, latency, size, SHA-256 of the body, Retry-After, fetch time. |
| `data_mode` is earned | `live` only when the candles were fetched during this call; injected or recorded data is reported as `unknown` with a warning. |
| Provenance is in the answer | `pair.venue_used`, every fallback attempt with its failure kind, `as_of`, and a one-line definition of every indicator used. |
| Status is derived | `ok` only if the candles and every clause were fully evaluated; `partial` otherwise; `unavailable` when no venue answered. Optional sections (cross-venue check) never lower the status. |
| Summary cannot disagree | The headline and key points are generated from `data`; a test parses the numbers back out and compares. |
| Refuse before charging for nothing | Malformed input raises a field-specific error with a working example **before any network call**. |
| Never guess a symbol | An unlisted symbol is reported as unlisted on every venue tried; no similar ticker is substituted. Stablecoins are refused. Wallet addresses are rejected. |

## Input

| Field | Required | Default | Notes |
|---|---|---|---|
| `symbol` | yes | | 2 to 12 letters/digits, e.g. `SOL`. Upper-cased. |
| `condition` | yes | | Expression tree, see below. At most 8 clauses, 3 levels. |
| `timeframe` | no | `1d` | `1h`, `4h`, `1d`. Coinbase has no 4h candles, so 4h uses another venue. |
| `lookback_bars` | no | `300` | Candles over which the history statistics are counted (50 to 1000). Indicators are computed on up to 200 extra earlier candles so EMA/RSI/ATR have converged. |
| `venue` | no | `auto` | `auto` tries Binance (and its data mirror), Coinbase, Kraken, Bybit in that order. |
| `cross_venue_check` | no | `false` | Also compare the last closed price with a second venue (tolerance 0.5%). |

**Condition grammar.** A group is `{"all":[...]}`, `{"any":[...]}` or `{"not": {...}}`. A clause is `{"left": operand, "op": op, "right": operand}` with `op` in `<  <=  >  >=  ==  cross_above  cross_below`. An operand is `{"value": number}` or `{"indicator": name, ...params}`:

| Indicator | Parameters (defaults) | Definition |
|---|---|---|
| `open` `high` `low` `close` `volume` | none | the raw field |
| `sma`, `ema` | `period` (required), `source` (close) | simple mean; EMA with alpha = 2/(n+1) seeded by the SMA of the first n |
| `rsi` | `period` (14), `source` | Wilder smoothing; 100 when average loss is 0; undefined when flat |
| `atr`, `atr_pct` | `period` (14) | Wilder ATR; the percent form divides by the close |
| `macd` | `fast` 12, `slow` 26, `signal` 9, `output` macd/signal/hist | EMA(fast) - EMA(slow); signal = EMA of that line |
| `bbands` | `period` 20, `stddev` 2, `output` upper/middle/lower/percent_b/width | SMA +/- k population standard deviations |
| `stoch` | `k_period` 14, `smooth` 3, `d_period` 3, `output` k/d | slow stochastic |
| `volume_zscore` | `period` (20) | spike versus the previous n bars (the current bar is excluded from its baseline) |
| `roc` | `period` (10), `source` | percent change over n bars |
| `highest_prior`, `lowest_prior` | `period` (20), `source` | extreme of the n bars **before** this one, for breakouts |

A `cross_above` clause is true only on the bar where the left side moves from at-or-below to above the right side; touching is not crossing.

## Four ways to call it

After the install in [Tests](#tests) (or `pip install -e .` in any Python 3.11+ environment), run `source .venv/bin/activate` so the `indicator-condition-check` command is on your PATH.

```python
from indicator_condition_check import run
envelope = run({"symbol": "SOL", "condition": {...}})          # Python
```
```bash
indicator-condition-check check --symbol SOL --condition @rule.json    # CLI (exit code 2 on invalid input)
indicator-condition-check mcp                                          # MCP over stdio
indicator-condition-check serve --port 8765                            # REST + MCP over HTTP
curl -s localhost:8765/tools/indicator_condition_check/call -d '{"symbol":"SOL","condition":{"left":{"indicator":"rsi"},"op":"<","right":{"value":30}}}'
```

The REST routes follow the names of RYO's builder API (`GET /health`, `GET /tools`, `POST /tools/<name>/call` with a bare argument object; failures return `{code, message, details, trace_id}`); the success wrapper, the tool-list shape and some error codes still differ from the live service and are being aligned. The MCP definition carries `title`, `inputSchema`, a nested `outputSchema` and `annotations` (`readOnlyHint`, `idempotentHint`, `openWorldHint`). The HTTP server refuses foreign browser origins (403), rate limits tool calls per client (429; a batch is charged one unit per request in it), bounds concurrency and drains oversized bodies before answering 413.

## MCP: both protocol eras, as the specification and the official SDKs define them

One endpoint (`POST /` over HTTP, or stdio) serves two eras. The behaviour follows the MCP specification and the official TypeScript and Python SDK servers; `tests/server_checks.py` checks every rule in the two tables below, and its docstrings name the source of each.

| Era | What a client sends | What it gets |
|---|---|---|
| Handshake (2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25) | `initialize`, then `tools/list`, `tools/call`, `ping`; JSON-RPC batches (2025-03-26 requires a server to accept them) | the requested revision if served, otherwise the latest (2025-11-25) |
| Stateless (2026-07-28) | every request carries `_meta` with `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`; `server/discover`, `tools/list`, `tools/call` | every result carries `resultType` and the server's name and version in `_meta["io.modelcontextprotocol/serverInfo"]`; `server/discover` lists `["2026-07-28"]` |

Errors, with their HTTP status on `POST /`:

| Case | JSON-RPC error | HTTP |
|---|---|---|
| Body is not JSON | -32700 | 400 |
| Not a JSON-RPC message (for example `"id": null`), or a bad batch | -32600 | 400 |
| Stateless request without `clientCapabilities` in `_meta` | -32602 | 400 |
| Stateless revision not served (also a 2025 revision sent through `_meta`) | -32022, `data: {supported, requested}` | 400 |
| `MCP-Protocol-Version`, `Mcp-Method` or `Mcp-Name` header missing or not matching the body (stateless era; `Mcp-Name` may use the `=?base64?...?=` form) | -32020 | 400 |
| Handshake-era request with an `MCP-Protocol-Version` header that is not one of the four served revisions | -32602 | 400 |
| Unknown method | -32601 | 404 stateless, 200 handshake era |
| Unknown tool name | -32602 | 200 |
| Invalid tool arguments | a result with `isError: true` and a working example | 200 |

Notifications and posted responses get `202` with no body; `GET`, `PUT`, `PATCH` and `DELETE` on `/` get `405` with `Allow: POST`; a `POST /` whose `Content-Type` is not `application/json` gets `415` (the REST route does not check it).

## Use it from an MCP client

Stdio (most desktop clients): point the client at the installed script with the argument `mcp`, for example

```json
{"mcpServers": {"indicator-condition-check": {"command": "/absolute/path/to/.venv/bin/indicator-condition-check", "args": ["mcp"]}}}
```

HTTP: start `indicator-condition-check serve --port 8765` (it listens on 127.0.0.1 only) and use `http://127.0.0.1:8765/` as the Streamable HTTP URL. Two calls that need no network:

```bash
curl -s localhost:8765/ -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -s localhost:8765/ -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":2,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx indicator-condition-check mcp` over stdio). The tests validate it against a recorded copy of the registry's schema (`tests/fixtures/registry/`). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. The line below is the ownership marker the registry looks for in a PyPI package's README.

<!-- mcp-name: io.github.RYO-Digital/indicator-condition-check -->

## Failure behaviour

| Situation | What happens |
|---|---|
| 429 / 5xx / network error | Full-jitter exponential backoff, `Retry-After` (seconds or HTTP date) honoured (an absurd value is capped at an hour; a wait longer than the call's own deadline is not slept: the venue is left alone for that long and the next venue is tried), two retries, then the next venue. After a 429 the venue is left alone for a cool-down, and a 418 ban is never retried (30 s), so a polling loop cannot turn a rate limit into a ban. |
| Same candles requested twice | Closed candles are immutable until the next candle closes, so they are reused from a per-process cache until then (`data_mode` becomes `mixed`, `data.cache` says so). If every venue fails, the newest cached set under six hours old is served, labelled stale with its age, `status: partial`; older than that, `unavailable`. |
| Coinbase caps a reply below the history needed | Pages backwards with `start`/`end` windows (at most 6 requests), de-duplicating candles. |
| 451 / 403 (region) | Not retried; the Binance mirror is tried; then the next venue. |
| Symbol not listed on a venue | Recorded as `not_listed`; next venue. If no venue lists it: `unavailable`, and the headline names the symbol. |
| Malformed or implausible reply | `bad_response`; never a crash. |
| Venue returns fewer candles than asked | The venue with the longest history is used and the shortfall is a warning. |
| Everything down | `status: "unavailable"`, `result: null`, the full attempt list. No verdict, in particular not `false`. |
| Time budget (25 s) exhausted | Remaining venues are reported as skipped. |

## Tests

Requirements: Python 3.11 or later and, for the first line below, [uv](https://docs.astral.sh/uv/). Without uv: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,reference]"`. Installing needs the network (the package index); the tests do not, except the two `RUN_LIVE=1` ones.

```bash
uv venv .venv --python 3.11 && uv pip install -e ".[dev,reference]"   # from this folder; reference = TA-Lib, used only by the golden tests
.venv/bin/python -m pytest -k "not every_change"   # 472 tests offline (2 more with RUN_LIVE=1); real recorded venue responses as fixtures
.venv/bin/python -m pytest          # the same, plus a mutation sweep of server.py (every small change of its code must be noticed by tests/server_checks.py)
RUN_LIVE=1 .venv/bin/python -m pytest -k "not every_change"   # adds two tests that call the real exchange APIs
.venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy src
```

What is covered: look-ahead invariance for every indicator and operator (values at bar i are identical when later bars are removed or replaced by absurd ones); degenerate inputs (0, 1, 2, 10 bars; flat, rising and zero-volume series); gaps, truncation, cache, stale-while-error, pagination, cool-down, clock skew; a stdio subprocess protocol test; golden vectors against TA-Lib on three real candle sets for RSI, ATR, SMA, EMA, Bollinger, stochastic, ROC and MACD (1e-9 relative tolerance); grammar rejection with field paths for 19 malformed conditions and 14 malformed requests; three-valued logic; cross events and boundaries; every venue's real reply shape plus its "symbol not found" shape; 451, 429 with `Retry-After`, 5xx, timeouts, malformed bodies, implausible candles, budget exhaustion; the envelope validated against its JSON Schema with strict JSON; summary numbers parsed back and compared with `data`; MCP over stdio and HTTP and REST against a real local server; every MCP rule in the two tables above, with exact error texts, in both eras, including batches and the headers; `server.json` against the registry schema, with sixteen broken variants the schema must refuse, and its launch arguments starting the stdio server; agreement between the published input schema and the runtime validator, plus a tool-definition snapshot so a contract change is always deliberate.

Fixtures are real venue responses recorded on 2026-10-20 (`tests/fixtures/README.md`). They are never presented as live data.

## Limits and what is not verified

* Spot pairs against USD (Coinbase, Kraken) or USDT (Binance, Bybit). Quote conventions differ slightly; `cross_venue_check` reports the difference. A 48-combination live sweep (4 symbols x 4 venues x 3 timeframes) returned usable candles everywhere except the expected Coinbase 4h refusal; RSI(14) for BTC and SOL agreed across venues within 0.53 points on 1h and within 0.12 on 1d (single snapshot, 2026-10-20).
* The result is research context, not a trade signal. It does not backtest the rule and does not claim the condition predicts anything.
* **Conformance to the organisers' "skill contract + honesty convention" is NOT VERIFIED.** That document was not published anywhere reachable on 2026-10-20. This skill follows what is public: the builder guide's response envelope and honesty rules, the `SkillDefinition` schema in the public OpenAPI, and the MCP tool-definition format. Packaging and registration requirements, if any, are unknown.
* The MCP behaviour was built from the specification text and the source code of the official TypeScript and Python SDKs; no official SDK client has been run against this server in this version (NOT VERIFIED that way). `server.json` validates against the registry's schema; the registry's own publish-time checks (namespace login, PyPI ownership) have not run, because nothing has been published.
* Not compared against RYO's own `analyze_token` RSI/ATR: that needs a builder key.
* A condition may also be sent as JSON text (some MCP clients do this); it is parsed and the fact is recorded in `request.notes`.
* Exchange rate limits for Coinbase, Kraken and Bybit were not measured; the skill backs off and falls back instead.
* Binance may be region-blocked for some users; the mirror and the other venues are the fallback.

## Licence

MIT (`LICENSE`). This skill is one of four standalone packages in the Hoshitori repository; its place in the product is described in the repository README.
