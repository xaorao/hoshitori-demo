# Gateway design

Scope: the gateway: URL safety and scheduling. Status: DESIGNED. The gateway is the only caller of RYO and of every other budgeted or external source (contract section 42). It implements the adapters' contracts (`CONNECTOR_CONTRACTS.md`) and produces the `RawCapture`s that the domain model starts from. Numbers marked prior are starting values for the exploratory period, fixed by measurement.

## 1. Shape

One module in the application process (not a separate service in the first release; the interface is the same if it is split later), with five parts:

| Part | Job |
|---|---|
| Admission | accepts a `Call(provider, operation, args, class, deadline, caller)`; rejects what is outside the catalogue or the allow-list; computes the cost with the adapter's `plan` |
| Catalogue | reads `GET /tools` at startup and on a schedule with a long timeout and retries (authenticated reads measured 55 to 161 s, a 120 s timeout and a 504; the per-attempt read timeout is above 165 s), caches it with a long life, serves admission from the cache, raises `CONTRACT_DRIFT` on a change and never blocks a cycle on a catalogue read; records the latency of every RYO call for the published per-tool distribution |
| Governors | one per provider and per budget unit (RYO calls, Binance weight, Hyperliquid weight, Coinbase tokens, news credits); each knows the provider's limit, its current remaining value (from response headers when the provider sends them), the reservations and the ban state |
| Scheduler | orders waiting calls by class, then by arrival; releases a call only when its governor can pay; honours declared collection windows |
| Executor | runs the adapter's `fetch` with the deadline, classifies the outcome, stores the `RawCapture`, updates governor, breaker and metrics |
| Breakers | one per provider and per tool (section 4) |

Callers: the cycle scheduler (`P0`), judge sessions (`PJ`), follow refresh (`P1`), on-demand investigations (`P2`), background work (`P3`), and the **attestation class `PA`** for the ledger writer's time-stamp, witness and public-log requests, handed over through the outbox (an outside review: the architecture said the gateway is the only egress and also gave the ledger writer token requests). `PA` uses providers no other class uses (each time-stamp authority, witness and log has its own governor), is served first on them, and is never starved. A page view calls nothing (contract section 42 item 5).

**Egress ownership.** Every outbound network connection of the system is made by the gateway: league and on-demand evidence fetches, model calls, news search, user-URL fetches through the safe client, and attestation requests. No other process (web/API, ledger writer, scheduler, workers, verifier service) has network egress; the network policy denies it and a test attempts it from each (an allowed destination from a non-gateway process is a defect). The Local Connector runs on the user's machine and is outside this boundary by design.

## 2. Cost and the RYO pool

RYO's pool is calls: 6 per minute in the only recording we have (RYO compatibility, section 7), one pool for every class. Two consequences designed in:

1. **Declared collection windows.** The cycle scheduler announces a window `(start, n_calls, deadline)` ahead of time. The gateway keeps `P2` and `P3` at zero from one refill period before the window and throughout it, so the first calls find the pool full and the rest follow the provider's own pacing. The governor supports both rate models and is told which applies by the compatibility probe: `FIXED_WINDOW` (reset time read from `X-RateLimit-Reset`) and `TOKEN_BUCKET`; until measured it assumes the more conservative, the fixed window. A window of 17 calls then needs windows at 0, 60 and 120 seconds, and the last call (up to 46 seconds, third-party) finishes about 2 minutes 46 seconds after the start. The scheduler refuses to publish a protocol whose collection window exceeds `D` (contract section 6, prior 10 minutes).
2. **The judge lane.** `PJ` has a floor: 1 call in every 6 (one token per minute, prior), served ahead of `P2`, `P3` and `P1`, never borrowed by them, and never taken from a declared window (a window's own calls are `P0`). When `PJ` has no tokens the judge sees a clearly labelled recorded run, never a blank page (contract section 42 item 1).

Everything else (`P1`, `P2`, `P3`) shares the remainder; `P2` has a per-identity cap (10 investigations a day for an unrated identity, prior) and a global daily cap.

Concurrency: up to 3 RYO calls in flight is a **prior, not a measurement**. Authenticated calls were measured at 50 to 161 s each (the third-party 20 to 46 s was not reproduced) and the delay sits before dispatch (credential verification and call allowance), so whether concurrent calls overlap or each stretches is unmeasured; the cycle plan is computed from that measurement. The bucket counts starts.

## 3. Reservation algorithm (deterministic, testable)

State per governor: `model` (`FIXED_WINDOW` or `TOKEN_BUCKET`), `tokens` (float) with `refill_per_s` and `capacity` (token bucket) or `window_start` and `window_limit` (fixed window), `ban_until`, `earmarks` (list of `(start, n, class)`).

1. On each tick (1 s) or arrival: refill, capped at `capacity`; update from the latest `X-RateLimit-Remaining` / `Retry-After` / weight header, which overrides local belief **unless it is stale**: a reset time at or before the response's own date means the window has already rolled and the remaining count is stale (observed: reset 48 s before the response date); headers are absent on 503, `/tools` and `/whoami`, so local accounting continues across responses that carry none.
2. Candidate order: `P0` and `PJ` first, then `P1`, `P2`, `P3`; inside a class, earliest deadline first, then arrival.
3. A candidate is released if `tokens >= cost` **and** releasing it would not take the governor below the sum of earmarks that fall due before the bucket could refill them (for `P1` to `P3` only); `P0` and `PJ` ignore earmarks.
4. A call that cannot finish before its deadline is not released: it is returned to the caller as `DEADLINE_UNREACHABLE` with the queue position it had.
5. After a `RATE_LIMIT`, every class backs off together until `Retry-After` (or the window); `P0` resumes first. After a `BANNED` outcome the provider's breaker opens for the ban length and nothing is sent.

Invariants (property-tested with simulated clocks): the governor never goes below zero; the sum of tokens spent in any window never exceeds capacity plus refill; `PJ` receives at least its floor over any 10-minute window in which it has demand; while an earmark is active, `P2` and `P3` receive nothing; a call is never started twice; the number of requests in flight never exceeds the concurrency cap.

## 4. Circuit breakers

| State | Entered when | Behaviour | Leaves when |
|---|---|---|---|
| `CLOSED` | normal | calls pass | N consecutive counted failures (priors: 5 for exchange reads, 3 for RYO) or error rate above 50 percent over 20 calls |
| `OPEN` | threshold crossed, or a ban | calls are refused immediately with `BREAKER_OPEN` and the reason; a single health probe per interval is allowed | the cool-down elapses (exponential, 15 s to 10 minutes) or the ban ends |
| `HALF_OPEN` | cool-down over | one trial call at a time | a trial succeeds (back to `CLOSED`) or fails (back to `OPEN`, longer cool-down) |

`RATE_LIMIT` does not trip a breaker (it is back-pressure, handled by the governor); `BANNED`, `AUTH`, `GEO_BLOCKED`, `CONTRACT_DRIFT` and repeated `MALFORMED` do. A breaker is per provider and, for RYO, per tool for `CONTRACT_DRIFT` and `MALFORMED` (one tool changing shape does not hide the five that still work), **plus one provider-level RYO breaker that opens on `UPSTREAM_UNAVAILABLE` from the shared pre-dispatch stage** (credential verification, call allowance) that every tool passes through; per-tool breakers alone would each trip and each probe separately.

What the user sees: an open breaker is a source-health chip with the provider, the class and the age of the last good item (data provenance, section 8); a scored cycle that needs that source ends `FAILED_EVIDENCE`.

## 5. Isolation from league evidence

Two namespaces: league captures are written by `P0` calls inside a declared window and are the only ones a cycle snapshot may contain; everything else is `ON_DEMAND`. An on-demand call **may read** a league capture that is still within the freshness limit (cache key: provider, tool, normalised arguments, `as_of`; public-source captures only, never a private text), and **never writes** into a league namespace. Arms cannot read `ON_DEMAND`. The frozen snapshot hash is stored at seal time; a test fails if any later write could change it.

## 6. Safe fetching of user-supplied URLs (A31)

A separate client, not the provider adapters, fetches URLs a person pastes or a source cites. Rules enforced in code (not in a prompt): scheme `https` only (and `http` only to a short allow-list when explicitly needed); the hostname is resolved by us, every resolved address is checked against private, loopback, link-local, multicast and metadata ranges (including IPv6 and IPv4-mapped forms) and the connection is made to the checked address; redirects are followed manually, at most 3, and each hop is checked again; a response body limit (default 2 MB) and a total time limit; content types allow-listed; no cookies, no credentials, no custom headers from the user; the fetched text is wrapped as untrusted data and never reaches a tool-calling context as instructions; the response digest is stored. This follows the OWASP SSRF prevention guidance (downloaded, read for its checklist) and is tested with a rebinding server, a redirect-to-private chain and oversized, slow and compressed-bomb bodies.

## 7. Observability

Every call emits one event: provider, tool, class, caller kind (never the user's text), queue wait, latency, status class, bytes, remaining budget, breaker state, trace id (the provider's where it gives one). Metrics: queue depth and wait by class (the judge lane's wait is its own SLI), budget remaining, breaker state, error classes, cost. Traces use OpenTelemetry names; claim text is never an attribute (privacy flows).

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| RYO returns 429 during a declared window | window pauses on `Retry-After`, resumes; if the deadline becomes unreachable the cycle is abandoned for the horizons it breaks | synthetic 429 trace with a simulated clock |
| Public flood of `P2` | judge request still served; flood receives `QUEUED` positions then `BUDGET_EXHAUSTED` | load test |
| Judge floor exhausted | recorded run labelled as such | forced exhaustion |
| Binance 418 | breaker open for the stated ban; settlement uses the second oracle | ban trace |
| Header says remaining budget lower than local belief | local value replaced | header-override test |
| Redirect to a private address | refused, event recorded | SSRF suite |
| Process killed mid-call | the call is recorded `ABANDONED` on restart; its cost is counted; it is not replayed unless the caller re-asks | kill test |
| Two callers ask the same read at once | one fetch, one capture (singleflight on the cache key) | concurrency test |

Connection to the journey: the gateway is where "keep working under rate limits, restarts and dependency failure" is visible: the page for an investigation shows queue position, which source is slow, and which checks are done; the judge lane is what lets a stranger complete the journey while the public queue is busy.

## 9. As built: the safe fetch client (build unit 4.9)

Code: `fetch_types.py` (reasons, results), `safe_fetch.py` (every rule that decides, with the name service and the network behind two small interfaces), `safe_fetch_net.py` (one request to an address that was already checked). Tests: scenario checks of the rules and a sweep of every small change of the code (`safe_fetch`: 311 places, 1 harmless; `safe_fetch_net`: 109 places, none harmless) with no packet sent from inside them; 30 end-to-end cases against real TLS servers on loopback.

- **Where it is stricter than section 6.** A host must be a *name*: letters, digits and hyphens, at least two labels, a last label of letters (or a punycode label), so no numeric form of an address is ever passed to a resolver. The source study showed why: macOS reads `0177.0.0.1` as the decimal `177.0.0.1`, a resolver that follows `inet_aton` reads it as `127.0.0.1`, and `ipaddress` rejects it, so a literal is refused rather than interpreted. IPv6 and IPv4 literals, public ones too, are refused. The address must be ASCII with no space, control character or backslash (a browser percent-encodes and punycodes first). Ports are 443, and 80 for a host that was named for plain http; the caller can widen the lists.
- **Resolution.** The name is resolved once per hop by our resolver (a thread, abandoned at the deadline). *Every* resulting address must be globally routable: an IPv4-mapped IPv6 address is judged by the address inside it, and the forms that carry an address inside another (`::/96`, `64:ff9b::/96`, `64:ff9b:1::/48`, `2002::/16`, `2001::/32`) are refused outright, as are multicast, shared carrier space, link-local (the cloud metadata addresses), scoped addresses and anything `ipaddress` does not call global. One address that fails makes the whole name refused, so a mixed answer does not pass. At most 8 of the checked addresses are tried, in the order given; a name with more than 64 is refused.
- **The connection goes to the address, not the name.** TLS is verified against the name that was asked for (the default trust store, or a named certificate file in tests); the `Host` header is the name, with a port only when it is not the scheme's. A server that answers a second look-up differently is never asked a second time; a redirect back to the same name is looked up and checked again.
- **Redirects** (301, 302, 303, 307, 308) are followed by us, at most three, each target re-read and re-checked from scratch (scheme, host, port, resolution), always as `GET`; a missing, repeated, long or unclean `Location` is refused. No cookie is kept or sent.
- **The answer.** Status 200 only. One `Content-Type` from a list (HTML, XHTML, plain text, JSON, XML, RSS, Atom); PDF and everything else is refused. A `Content-Encoding` other than `identity` is refused and never decoded, so a compression bomb cannot expand; we ask for `identity`. The announced length is checked against the limit before the body is read, the body is cut at the limit while it is read (2 MB by default), and an answer shorter than it announced is a failure. One deadline (20 s) covers the name look-ups, the redirects and every read: a watchdog shuts the socket.
- **What comes back and what is recorded.** `Fetched` holds the bytes, the media type and charset, the SHA-256, the redirect chain and the address connected to. Events (`request`, `resolved`, `redirect`, `refused`) have the address redacted; a refusal carries them. A refusal has a code (`FetchCode`) and a class in the connector taxonomy; a refusal of the caller's input is `INVALID_ARGS` and is never retried.
- **Checked on the real internet (2026-10-29, by hand, not a test):** `https://example.com/` and `https://www.iana.org/domains/reserved` fetched over IPv6; `http://example.com/`, `https://127.0.0.1/` and `https://localhost/` refused before any packet; an expired and a self-signed certificate refused; a gzip answer refused.
- **Not verified.** Behaviour with a system resolver other than macOS's; the sweep notes one change that is harmless only on Python 3.12 (`is_global` already looks inside a mapped address); HTTP proxies are not used and not supported; IDN addresses must be punycoded by the caller; the client is not yet wired into the gateway's queue (the gateway asks it), and the claim and challenge code that will call it is not built.
