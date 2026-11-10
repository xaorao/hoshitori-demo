# Data provenance

Scope: evidence, point-in-time provenance, independent origins. Status: DESIGNED. Product rules are in the contract, sections 16, 18, 21, 28, 29, 40; this file defines the structures and the algorithms that enforce them. Sources studied: W3C PROV-O (vocabulary), OpenLineage (job and dataset lineage, rejected as the model, see section 4), Qlib's point-in-time store (read), CloudEvents (study ledger).

## 1. Principles

1. **Raw bytes first.** Every external response is stored as received (`RawCapture`) before anything reads it. An `EvidenceItem` is a typed reading of a capture, never a replacement for it.
2. **Four times, never one.** `event_time`, `first_seen_at`, `received_at`, `known_at` (domain model, section 2). An item can inform a decision only from its `known_at`; a later backfill with an earlier `event_time` does not change what the system knew.
3. **Unknown is not zero.** A missing, null or unavailable measurement is absent from `fields` with a reason; a numeric 0 is only ever a measured 0 (section 6).
4. **Counting origins, not calls.** Independence is counted over origin families (section 3), after collapsing repeats of one original.
5. **Lineage is declared, not inferred.** Every derived item names its parents and the transform version that computed it.

## 2. The evidence item

| Field | Type | Notes |
|---|---|---|
| `evidence_id` | id | natural key: `(capture id, adapter version, item path)`; a derived item: `(transform, version, parent ids, path)`. The content hash is an attribute, never the identity (CF-26, E19) |
| `capture_id`, `raw_sha256` | refs | the stored bytes |
| `kind` | enum | `OBSERVATION` (a measured value from a source), `DERIVED` (computed by us from parents), `INTERPRETATION` (a vendor or model judgement: RYO `verdict`, confluence, trade plan, any summary) |
| `source`, `tool`, `request` | strings, object | normalised request; for RYO the `request` object RYO echoed |
| `status`, `data_mode`, `availability`, `warnings` | as received | RYO envelope fields kept verbatim; `data_mode` other than `live` blocks scoring |
| `fields` | object | decimal strings keyed by stable field names; absent keys have an entry in `unknown_fields` |
| `unknown_fields` | array | `{path, reason}` with reasons `NULL`, `ABSENT`, `UNAVAILABLE_BY_AVAILABILITY`, `MALFORMED`, `DEGRADED_ZERO` |
| `event_time`, `first_seen_at`, `received_at`, `known_at` | instants | `known_at = received_at` unless a source defines a publication time we can verify. The `event_time` of a candle series is the venue's own clock when it answered (CF-46), not the close of its newest bar, so the 60-second price limit judges the reading and not the bar grid |
| `data_mode` | text | `live` for what an exchange or RYO said just now, `recorded` for a replay, `mixed` for a derived item whose parents differ (CF-51). A scored bundle needs every cited evidence item to be live; an item with no mode is `missing` in the verifier and makes the bundle not scorable |
| `origin_family` | string | section 3 |
| `derived_from` | array of `{evidence_id, sha256}` | roots have none |
| `transform` | `{name, version}` | for `DERIVED` |
| `supersedes` | id or null | a revision appends a new item and never edits the old |
| `asset_refs` | array | `(asset_id, instrument_id, status)` from the registry; an asset id names one token and one instrument (CF-46). Evidence about a candidate carries one resolved reference to its asset; evidence about the whole cycle carries none |
| `coverage` | object | what was requested and what returned (section 7) |
| `redistribution` | enum | `PUBLIC_OK`, `PRIVATE_ONLY`, `DERIVED_ONLY` (data rights, section 9) |

The vocabulary follows W3C PROV-O: `derived_from` is `prov:wasDerivedFrom`, `capture_id` is the entity a `prov:wasGeneratedBy` activity (the fetch) produced, a decision `prov:used` its evidence, `supersedes` is `prov:wasRevisionOf`, a quotation is `prov:wasQuotedFrom`, and a corrected source is `prov:wasInvalidatedBy`. An exporter to PROV-JSON is a later feature; the internal model stays our own JSON.

## 3. Origin families

| Family | Assigned when | Notes |
|---|---|---|
| `RYO` | any RYO tool | **one family for all tools**: third parties report a shared backend, and a shared upstream cannot be excluded; a sub-label records the tool. Splitting is a versioned change that needs evidence |
| `EXCHANGE:<venue>` | exchange adapters | two venues quoting from a shared index are listed as a pair and counted as one; unknown upstreams read "independence not established" |
| `CHAIN:<network>` | chain adapters | two explorers on one RPC provider are one family |
| `PRIMARY_DOC:<publisher>` | exchange announcements, filings, project posts that are the original | the strongest class; the first fetch is stored with its hash |
| `NEWS:<outlet>` | news adapters | outlets that repeat one original are collapsed into that original's family by the narrative-origin graph |
| `SOCIAL:<author>` | social adapters | provenance level SP-1 to SP-4 (contract section 28) travels with it |
| `DERIVED` | never an origin | inherits the families of its parents |

Algorithm (`independence_count(conclusion)`): take the supporting items; replace each `DERIVED` item with the families of its parents (recursively); collapse items that cite one original (same canonical URL, same content digest, or a narrative-origin cluster) into the original's family; count distinct families; report the contradicting items' count the same way and show both. Three RYO calls give 1; an outlet quoting an exchange announcement adds nothing beyond the announcement's family.

## 4. Lineage graph and its uses

- **Store:** a table `edge(child_id, parent_id, transform, version)` derived from `derived_from`; it is a DAG (a cycle is a rejected write).
- **Descendants** of an item are computed by a recursive query. Three consumers: the evidence-challenge engine removes a source **and every descendant** (contract section 29) and runs the leak check; the independence count above; the "what changed" diff.
- **Opaque vendor values** (for example RYO's RSI, whose inputs and formula we cannot see) are treated as descendants of the price family; the standard bundle withholds prices together with vendor technicals, and an intervention that leaves one while removing the other is labelled partial.
- **Why not OpenLineage:** it models jobs, runs and datasets (a scheduler's view: run events with inputs and outputs, UUID run ids, facets) and attaches lineage to pipeline executions; we need item-level derivation with explicit time semantics. Apache-2.0; read (specification core and naming); not adopted; the facet idea (typed extensions that old readers ignore) matches our must-ignore rule.
- **Why not DataHub:** a metadata platform with its own service; listed in the source map, not studied beyond its purpose (NOT STUDIED); its scope is far above a single-product lineage.

## 5. Point-in-time reads and revisions

- A read "as of D" selects, for each key `(source, quantity, subject, period)`, the item with the greatest `known_at <= D`; a revision is a new item with a later `known_at` and `supersedes` set. This is the shape of Qlib's file store (each row carries the statement's publication date, the period it describes, the value and a pointer to the next revision of the same period), without its restriction to quarterly fundamentals or its float storage.
- A decision records `observation_set_hash` over the exact ids and hashes it used, so a later revision can never change what the decision "saw".
- **Corrections and deletions** of news and social items create a new item (`INVALIDATED` or `REVISED`) and leave the original; derived claims that cited the original are re-evaluated by new checks, not edited.

## 6. Unknown versus zero (field contracts)

Every field of every adapter has a contract row, generated from the live catalogue where one exists and committed before the first scored period:

| Property | Meaning |
|---|---|
| `kind` | observation, derived, interpretation |
| `unit` | percent, USD, ratio 0 to 1, count, label |
| `zero_valid` | whether a measured 0 is a legitimate reading |
| `unusable_when` | availability or status values that make the field unusable whatever its value |
| `null_means` | `NOT_REPORTED`, `NOT_APPLICABLE` (for example `chain` on a symbol lookup) or `UNAVAILABLE` |

Rules (as built, CF-44: the section an answer reports availability for is not known for every tool, so "the section's availability" is judged over the whole answer, which errs towards unknown): (1) a numeric 0 on a field with `zero_valid = false` (a price, a market cap) is `MALFORMED`; (2) a 0 on a `zero_valid` field is kept only if the section's `availability` is the tool's ok value; otherwise it is `DEGRADED_ZERO` and treated as unknown (third parties report RYO returning zeros and a neutral label in degraded mode while flagging fields available; NOT VERIFIED by us); (3) `null` is never converted; (4) a mean, sum or correlation over a window with unknown members is computed over the known members with the count reported, or is itself unknown, never over zeros. The cautionary example is an open issue in a finance-agent project that publishes `0.0` for undefined (NaN) correlation pairs (Vibe-Trading issue 1932, from the landscape study; the issue was read there and not re-opened by us).

## 7. Source coverage auditor

Each source adapter publishes per window: items requested, items checked, items failed with reason, the last successful observation time, missing intervals, and the provider's stated limits. A statement of absence ("no mention", "no listing", "no whale activity") is allowed only if coverage meets the pre-registered threshold for that source and window; otherwise the text is "not observed (coverage incomplete)". The Today surface shows the coverage line beside any such statement. The auditor is also the data source of the "RYO unavailable" chip and of the SLIs in `OPERATIONS_SLOS.md`.

## 8. Last-good handling

When a source fails, the system may serve its last good item **only** with its age and a visible `STALE` label, and only to surfaces that are not scored cycles; a scored cycle with a required source down is `FAILED_EVIDENCE` (contract section 5), never a decision on stale data. The staleness limits are the per-source values of contract section 16.

## 9. Retention and data rights

`PUBLIC_OK`: derived numbers and permitted payloads appear in public bundles. `PRIVATE_ONLY`: raw third-party payloads (RYO responses pending the organisers' answer; exchange payloads pending terms review; any user-submitted content) are stored encrypted and shown to authorised reviewers only. `DERIVED_ONLY`: only hashes and derived values leave the system. Deleting a private payload leaves the digest in the ledger; a record that depended on it reads `NOT RECONSTRUCTIBLE` (contract section 8, 36). The data-use terms of each venue and of RYO are **NOT VERIFIED** and are a gate before any public redistribution.

## 10. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Evidence arrives with `known_at` after the decision's `as_of` | invisible to that decision | point-in-time read fixture with a backfilled item |
| A revision lands | new item; decision still points at the old one by hash | revision fixture |
| Adapter drops a field | `unknown_fields` entry with `ABSENT` | schema-drift fixture |
| RYO degraded zeros | `DEGRADED_ZERO`, treated unknown | recorded degraded response |
| Three RYO items support a claim | independence count 1 | origin-family fixture |
| Descendant left after a source is removed | leak check fails the run | lineage fixture |
| A cycle in `derived_from` | write rejected | graph fixture |
| Coverage below threshold | "not observed (coverage incomplete)" | coverage fixture |
