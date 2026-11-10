# Connector contracts

Scope: connectors, throttling, source failure. Status: DESIGNED; limits are quoted from the providers' own documentation or response headers and say which. Sources read: Binance spot API documentation (cloned, `rest-api.md`), Hyperliquid rate-limit and info pages, Coinbase Exchange rate-limit page, Kraken REST rate-limit page, CCXT manual (error taxonomy, unified market structure), tenacity (retry vocabulary), Toxiproxy (fault vocabulary). The gateway that enforces these is `GATEWAY_DESIGN.md`.

## 1. The adapter interface

Every provider is wrapped by one adapter with the same four operations. Adapters never call each other and never talk to the model.

| Operation | Contract |
|---|---|
| `plan(request) -> Cost` | returns the cost in the provider's own unit (RYO calls; Binance request weight; Hyperliquid weight; Coinbase requests) before anything is sent, so the gateway can reserve budget |
| `fetch(request, deadline) -> RawCapture` | one read-only HTTP exchange; returns the bytes, status, headers, timing; never retries by itself; raises one of the typed errors of section 2 |
| `parse(capture) -> list[EvidenceItem]` | pure function; rejects anything outside the pinned schema with `MALFORMED`; never reads the network; is the unit under golden-fixture test |
| `health() -> Health` | cheap, free where the provider allows (RYO `/health` and `/whoami` are free), used by breakers and the status page |

`fetch` has no side effects on the provider (all listed providers are used read-only), which is what makes bounded retry safe. A provider call that is not idempotent does not exist in this product.

## 2. One error taxonomy for all providers

CCXT's hierarchy (`NetworkError`, with `DDoSProtection`, `RateLimitExceeded`, `ExchangeNotAvailable`, `RequestTimeout` below it, plus `InvalidNonce`, `OnMaintenance`) is the reference for the vocabulary; ours is smaller because we only read.

| Class | Typical signs | Retry | Breaker counts it |
|---|---|---|---|
| `RATE_LIMIT` | HTTP 429, RYO `Rate limit exceeded` text, Hyperliquid weight exhaustion | after `Retry-After` (or the window), once, by the gateway only | no, it is back-pressure |
| `BANNED` | Binance HTTP 418 | never until the stated end | yes, opens the breaker for the ban length |
| `AUTH` | 401, 403 (not geographic) | never | yes |
| `GEO_BLOCKED` | HTTP 451 or an equivalent body, connection refused from the host | never from that host | yes, and recorded: venues differ by host region |
| `TIMEOUT` | no response within the per-operation deadline | twice at most, with jitter, within the request deadline | yes |
| `UPSTREAM` | 5xx, connection reset, TLS failure | bounded (section 4) | yes |
| `MAINTENANCE` | provider status or a documented maintenance code | no, wait for the provider's signal | yes |
| `INVALID_ARGS` | 4xx caused by our request | never; a bug | no, but alarmed |
| `NOT_FOUND` | unknown symbol or pair | no | no; the registry decides if the symbol is supported |
| `CONTRACT_DRIFT` | schema or catalogue differs from the pinned one | never | yes, per tool |
| `MALFORMED` | 200 with a body that fails the schema | no | yes (counted per tool) |
| `UNCLASSIFIED` | anything else | as `UPSTREAM`, logged for review | yes |

## 3. Provider contracts

Numbers marked (doc) are from the provider's documentation read for this study; (hdr) from response headers observed today; (3p) from a third-party recording and not verified by us.

### RYO (`/api/mcp`, REST path)
Auth: bearer key, server side only. Cost unit: tool calls. Limit: 60 per minute per key (hdr in recordings; `X-RateLimit-*`), plus a 6 per minute fan-out bucket (3p). Errors, shapes and rates: `RYO_API_COMPATIBILITY.md`. Time fields: envelope `as_of` (seconds). Completeness: `status`, `availability`, `warnings`. Retry: only after the rate-limit window; `INVALID_ARGS` never.

### Binance spot (public market data)
Base: `api.binance.com`, with the market-data-only mirror `data-api.binance.vision` also answering today (hdr). Cost unit: request weight; limit 6,000 weight per minute per IP (`exchangeInfo.rateLimits`, hdr; documentation: limits are per IP, not per key). Klines weigh 2 (doc), `exchangeInfo` with a symbol weighed 20 (hdr `x-mbx-used-weight-1m`). A 429 means back off; **repeated violation yields an automatic ban (418), 2 minutes to 3 days, scaling for repeat offenders (doc)**; `Retry-After` gives seconds. Times are milliseconds (microseconds on request). Klines: identified by open time; the response may include the unfinished bar; use `/api/v3/time` and the bar's close time to drop it. Pagination: `limit` ≤ 1000. Our share: at most 20 percent of the IP budget (1,200 weight per minute, a prior), because other services may share the address.

### Binance USD-M futures (instrument list only)
`fapi.binance.com/fapi/v1/exchangeInfo` answered with 936 symbols (hdr, 1.16 MB). Used only to build the registry (1000x aliases, USDC variants); fetched at most daily.

### Hyperliquid (public `info`)
Limit: aggregated REST weight 1,200 per minute per IP (doc). Weights: most info requests 20; `l2Book`, `allMids`, `clearinghouseState`, `orderStatus`, `spotClearinghouseState`, `exchangeStatus` 2; **`userRole` 60** (doc); `candleSnapshot` adds weight per 60 items; history endpoints add weight per 20 items; `userFills` returns at most 2,000 fills and only the 10,000 most recent are retrievable (doc), so a missed window cannot be recovered. A full role lookup of the 47,569 leaderboard rows would take about 39.6 hours of the whole budget (contract section 21); the whale module therefore uses a cohort, caches roles and prefers streams. Names: perpetuals by `name` (`kPEPE`), spot by `@index` or `PURR/USDC` (doc and live), HIP-3 by `dex:name`.

### Coinbase Exchange (public)
10 requests per second per IP, bursts up to 15, lazy-fill token bucket, 429 on excess (doc). `GET /time` answered (hdr). Our share: 3 per second.

### Kraken (public)
`AssetPairs`, `Time` answered (hdr). The documented counter model (each call adds 1, ledger calls 2, decays by tier) applies to authenticated calls; the public-endpoint limits were **not read**: usage stays under 1 request per second per process until read.

### OKX (public)
Timed out from this machine (hdr). Unverified. Checked from the target host before it is relied on as the second USDT oracle.

### News and social
Tavily (recommended by the organisers; the free researcher plan is 1,000 credits per month per the brief): a monthly credit budget in the gateway, `RATE_LIMIT` when exhausted, items labelled `NEWS:<outlet>`. Bluesky public AppView and Telegram public channel pages: `SOCIAL`, level SP-1 or SP-2, per-source budgets, ordinary caches. X: only through the user's Local Connector (SP-3), never from the server. Details in `SOCIAL_SOURCE_GATEWAY.md`.

### Time sources
NTP: two reference servers; offset measured at start and hourly; `time.apple.com` measured +0.023 ± 0.106 s today. The ledger refuses to commit if the offset exceeds 1 second. Each exchange's own server time is read at settlement.

## 4. Retries, timeouts and budgets

- **One retry layer.** Only the gateway retries. Adapters do not, models' SDKs are configured with zero retries for provider calls, so retries never multiply.
- **Policy** (tenacity's vocabulary: `wait_random_exponential` for full jitter (`wait_exponential_jitter` adds jitter to the base and never waits less than it, reproduced), `stop_before_delay` with a per-attempt timeout no longer than the remaining deadline (`stop_after_delay` overshoots, reproduced), `retry_if_exception_type`, a `before_sleep` hook that writes a gateway event): retry `TIMEOUT` and `UPSTREAM` with exponential backoff (base 0.5 s, factor 2, full jitter, cap 8 s) and a wait of `max(Retry-After, backoff)` (RYO's 503 carries `retry-after: 5`); the request **deadline**, not an attempt count, stops retries (two retries of a 161 s attempt would stretch one call to about eight minutes); a timed-out attempt is counted against the allowance as if it had been charged; the reset header stands in for `Retry-After` only on a 429 that has none; never retry any other class; never retry inside a rate-limit window.
- **Deadlines:** every operation has a per-operation deadline and a per-request deadline set by the caller (a league cycle's collection window, an interactive request's patience). A call that cannot finish before its deadline is not started.
- **Budgets:** per provider and per priority class, with reservations (gateway design, section 3). Exhausting a budget is a state with a name (`BUDGET_EXHAUSTED`), a user-visible consequence and a metric.

## 5. Per-connector tests

1. **Golden fixtures**: recorded real responses (one per tool or endpoint, plus partial, unavailable, empty, huge) parsed by `parse`; expected `EvidenceItem`s stored beside them.
2. **Contract tests** (live, scheduled): the schemas and catalogue; failure marks the tool `CONTRACT_DRIFT`.
3. **Synthetic fault traces** replayed through a fake provider: 429 with and without `Retry-After`, 418 with a ban length, 5xx burst, slow response, connection reset, truncated body, an HTML error page with HTTP 200, a response whose declared and actual lengths differ, a clock skew in `Date`.
4. **Network faults** with Toxiproxy between the gateway and a real or recorded provider: `latency`, `down`, `timeout`, `reset_peer`, `slow_close`, `slicer`, `limit_data`, `bandwidth`, `packet_loss` (the toxics listed in its README, read); each has an expected class and an expected user-visible state.
5. **Malicious provider payloads**: oversized bodies, deeply nested JSON, strings with control characters or injection text, numbers as huge exponents, duplicate keys; the response must end in `MALFORMED` with the capture stored and nothing partial used.
6. **No leakage into league snapshots**: an on-demand fetch of a symbol in a cycle must leave the frozen snapshot hash unchanged (contract section 42).
