# Turning RYO on

RYO is off by default. A league that does not name it asks RYO nothing, its manifest says the scan route is off, and its cycles are byte for byte what they were before this code existed. This page says what turning it on does, what it costs in calls, what can go wrong, and what has and has not been checked.

Everything below was tested against a loopback fake of RYO and against recordings of the real service, and then measured live: one session of 31 read-only calls with the builder key, and two cycles of a scratch league with RYO on (the first read the scan and the market tools; the second, after the order of the reads was corrected, committed 15 forecasts and 3 no-calls at level C3 and passed both offline verifiers). What the live service does at other hours, and under a longer run, is not verified.

## What it adds to a cycle

1. **A scan, before discovery is fixed.** `scan_market` is asked for the protocol's top N. Its answer becomes the manifest's `RYO_SCAN` route: RYO's own rank and symbol per candidate, and the stored response they were read from (named in the manifest by capture id and hash). A candidate still has to be joinable by the registry and have a second oracle; RYO proposing it is not enough.
2. **Evidence for every candidate.** `analyze_token` and `deep_analysis` (with perpetuals) by symbol, and `market_overview` and `monitor_market_sentiment_shift` once for the whole cycle.
3. **Two items per answer, never one.** The fields the evidence-class table calls observations go in an observation item of the RYO origin family; the fields it calls interpretations (verdicts, trend labels, prose) go in a separate interpretation item. The snapshot lists the first for the independent arms and never the second. Both are committed in the cycle's batch.

4. **RYO's own read (arm R).** Each deep analysis that answers gives a read of RYO's verdict word and its preview plan, committed in the batch beside the agents' calls and listed in no snapshot. A read is scored only by the same cycle's settled 24-hour window on its token (right or wrong, never a probability), and its plan is settled on that window as a long barrier by the agents' barrier rule. The reading of RYO's words is a fixed, versioned table printed on the Track record; `cautious` is read as advice against buying, not as a forecast of a fall.

A number is kept as RYO spelled it. A null is unknown. A zero in an answer that was not wholly available (status not `ok`, or a section neither `available` nor `not_requested`) is unknown too, never a measurement.

## What it costs

| Per cycle | Calls |
|---|---|
| Scan | 1 |
| Quick read of each candidate | N |
| Deep read of each candidate | N |
| Market overview and sentiment shift | 2 |
| **Total** | **1 + 2N + 2** (23 for ten candidates) |

The league keeps a budget of `calls_per_hour` attempts to RYO (40 in the template). It is counted from the capture store, retries included, so a restart does not reset it. The tool-list read at start is a capture too and is counted, though RYO charges no quota for it, so the count errs on the side of asking less. When the hour is spent the scan is not made and the manifest says `RYO_BUDGET_SPENT`. When it covers the scan but not everything after, calls are spent in the order of what matters: every quick read, then the two market tools, then the deep reads; what the budget does not cover is not asked and is recorded as a failure that says so. At 23 calls a cycle the budget allows one full cycle an hour; set the schedule to match, or raise the budget by agreement with the organisers.

RYO enforces two limits. Its headers and `/whoami` say 60 calls a minute. A stricter limit of six a minute for the fan-out of tool calls is in no header: passing it answers HTTP 429 with `retry-after: 60`. The league therefore spends at most five calls a minute (a bucket that refills steadily) and ignores the headers' counts, which describe the looser limit. A cycle of ten candidates asks 23 times, so its RYO reads take at least four and a half minutes.

## Time

In the measured session `analyze_token`, `market_overview`, `scan_market` and `monitor_market_sentiment_shift` answered in 2 to 4 seconds, `deep_analysis` in 23 to 46 and `compare_tokens` in 39 to 45; the first recordings had 40 to 161 seconds for the same calls and two tools that answered `503` (they answered in the measured session; the cause is not known). The waits below are kept long. So:

- the scan waits up to `scan_wait_s` (200 in the sample below) before discovery is fixed;
- the evidence waits up to `collect_wait_s` (480 in the sample below: ten candidates are 22 calls, at least four and a half minutes at five a minute) in total, from one time limit for the whole collection, not one wait per answer;
- a snapshot may be taken at most ten minutes after discovery, which the two waits and the venue collection must fit inside. RYO is read before the venues, because the exchange series must be under a minute old at the snapshot and a read of RYO takes minutes.

Calls are read first and the exchange series last: RYO's reads take minutes at five a minute, the exchange series must be under a minute old at the snapshot, and the first live cycle that read the exchanges first had every slot fail with `NO_FEATURES` for that reason. A cycle of many candidates can also age its first RYO reads past their limit of 300 seconds; the cycle then keeps what is fresh and names the rest as stale. **Concurrency does not help:** four calls started together were answered twice with 429.

## What happens when RYO is wrong, slow or down

| Situation | What the league does |
|---|---|
| Scan call fails, times out, is unavailable, is not `live`, is empty or unreadable | The scan route contributes nothing; the manifest names a closed reason (`RYO_SCAN_*`); the cycle goes on with the other routes |
| The hour's budget is spent | `RYO_BUDGET_SPENT`; nothing is asked |
| An answer is not `live`, is `unavailable`, is about another token than the one asked, or does not fit the contract | Refused, recorded as a failure naming the tool; nothing from it is evidence |
| One tool fails for one candidate | The candidate keeps what it did get; the failure names the tool |
| Every call fails | The cycle still commits; no RYO item exists; the failures are in the outcome |
| The tool list has changed shape | The league reads it once before the first cycle and prints `ryo_catalogue: drift` with the tools; those tools are unavailable until a person has looked |
| The tool list cannot be read | `ryo_catalogue: not read` with the reason; the league goes on |

RYO is an optional source here: a cycle that has no RYO evidence is a cycle with fewer sources, not a failed cycle.

## How to turn it on

1. Put the key in an environment variable. It is read at each request and never written to a file, a record or a log.
2. Add the member `ryo` to the settings file:

```json
"ryo": {
  "url": "https://app-ryochan.com/api/mcp",
  "key_env": "RYO_MCP_KEY",
  "calls_per_hour": 40,
  "scan_wait_s": 200,
  "collect_wait_s": 480
}
```

`daily_hour` is optional: a UTC hour (0 to 23) at which the one cycle a day that reads RYO is planned. Every other cycle asks RYO nothing and its manifest says `RYO_READ_ONCE_A_DAY`. With a monthly allowance of about 200 calls and five candidates (13 calls a cycle), one cycle a day uses about 390 calls a month, so a key of that size also needs the protocol's `scan_n` and the watchlist kept small; without `daily_hour` every cycle reads RYO within the hour's budget.

**Budget table.** A cycle that reads RYO makes 3 fixed calls (`market_overview`, `scan_market`, `monitor_market_sentiment_shift`) and 2 per candidate (`analyze_token`, `deep_analysis`), so `3 + 2n` calls for `n` candidates (13 for the default five; the figure comes from the cycle plan, not from a measurement of a live key). With `daily_hour` set (one reading cycle a day, 30 days):

| Monthly allowance | Candidates per reading cycle (the protocol's `scan_n`) | Calls a month |
|---|---|---|
| 200 | 1 | 150 |
| 400 | 3 | 270 |
| 600 | 5 | 390 |
| 1,000 | 5 | 390, with room for the Desk's capped runs |

`python scripts/ryo_budget.py ALLOWANCE --event-cap E --watchlist W` prints the largest scan cutoff an allowance pays for with your event cap and watchlist (the candidates of a cycle are at most the three together).

The Desk adds at most 1 RYO call per run and 3 runs a day. A reading every other day halves the figures. Pick the row at or below your allowance; the league's own hourly budget (`calls_per_hour`) is a second, separate limit.

`calls_per_minute` is optional and defaults to 5, one under the stricter limit measured on the builder key (see "What it costs"). Name it only for a key that has been measured to allow more; a loopback fake in a test sets it high so that the check does not wait for real time.

3. Make the protocol with `--optional-source ryo`. The protocol pins that RYO is read, so the league refuses to start if the settings and the protocol disagree in either direction.
4. Start the league. Its first output line is the tool-list line above; `hoshitori-league status` then reports `ryo: {calls_last_hour, calls_per_hour}`.

Absent from the settings file, `ryo` means off, and `status` reports nothing about RYO.

## What was checked, and what was not

Checked, with scenarios that were seen failing and with every small change of the code noticed or listed as harmless: the answer-to-evidence conversion, the scan, the per-candidate and cycle reads, the budget, the attachment to the gateway (key read per request, a missing key fails the call and sends nothing, an attempt is never shorter than RYO's own timeout), the tool-list drift path, the settings, the cycle with RYO on and off, the run command.

Not checked: RYO's behaviour at other hours or over a longer run; the 429 recovery beyond `retry-after`; a cycle of ten or more candidates (the live cycles had three); whether `compare_tokens` and `market_overview` answered (both returned `503` in the first recordings and answered in the measured session; this code does not call `compare_tokens`); the real shapes of an error that validation would raise (every invalid-argument probe was answered by the credential stage); how a long-running league behaves over days.

## Not built here

`compare_tokens` and the live measurement of a long run. Arm R (RYO's own read) is built and tested end to end against a loopback RYO; no live read is in the record, because RYO refuses the key's calls: on 9 Nov RYO's own key status (read without spending a call) reported 200 calls a month, none used, and `allowed: false`.
