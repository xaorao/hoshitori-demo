# Domain model

Scope: the shared truth model. Status: DESIGNED; grounded in the studied sources and experiments E1, E2a, E2b, E2c, E3 (`../experiments/`). Product rules live in `FORECAST_SETTLEMENT_CONTRACT.md` (the contract) and are cited by section, never restated. This file defines the structures that carry them.

## 1. Components and who owns which fact

One authority per fact. A component may read another's records by id and content hash; it may not define a second version of them (standard 7). Writers are processes; the ledger is written only by the ledger writer (section 6).

| Component | Owns (writes) | Reads | Never does |
|---|---|---|---|
| Gateway | `RawCapture` (every external response: bytes, headers, timing), budgets, health | none of ours | parse meaning; decide anything |
| Evidence | `EvidenceItem`, `Asset` (registry), `SourceCoverage`, origin-family assignment | `RawCapture` | call a model; write forecasts |
| Claims | `Claim`, `ClaimGraph`, `SupportCheck`, `FalsificationRecord` | `EvidenceItem` | change an evidence item |
| Research | `Investigation`, `Check`, `Hypothesis`, `Decision`, `NextCheck`, `ChallengeRun` | `EvidenceItem`, `Claim` | write the ledger directly; fetch outside the gateway |
| Forecast and settlement | `Cycle`, `Manifest`, `Snapshot`, `Forecast`, `Fill`, `Settlement`, `Book` | all of the above by id | edit a committed record |
| Ledger | `LedgerRecord` (the canonical append), `Checkpoint`, `Commitment`, `Bundle` | the records it is asked to append | interpret a record's meaning |
| Verification | `VerificationResult` (derived, never authoritative) | bundles only | read the live database |
| Social | `Identity`, `Contribution`, `Appeal`, `ArenaEntry`, `Reputation` (derived) | `Forecast`, `Settlement` | alter a forecast or an outcome |
| Presentation | view models, rankings, notifications (derived, disposable) | everything, read-only | write a fact |

A derived object can be deleted and rebuilt from the ledger and the raw captures; an authoritative one cannot. The rebuild is a test (section 9).

## 2. Instants: every time has one name, one writer and one clock

A single `time` field is how timing bugs start (CloudEvents' optional `time` may mean the occurrence or "some other time"; study ledger entry A03). 
| Name | Meaning | Written by | Clock |
|---|---|---|---|
| `event_time` | when the source says it happened | adapter, from the source | source's, untrusted |
| `first_seen_at` | when a provider first showed it, if the provider says | adapter | provider's |
| `received_at` | when our gateway finished receiving the bytes | gateway | host clock |
| `as_of` | the evidence time a decision claims (the latest `received_at` it used, or the vendor `as_of` where vendor-defined) | agent harness | derived |
| `created_at` | when a record was built | producer | host clock |
| `committed_at` | when the ledger appended it | ledger writer | host clock |
| `attested_at` | latest `genTime` among the independent tokens the level requires | verification | the authorities' |
| `eligible_after` | `attested_at` + u | forecast | derived |
| `authorized_at`, `forecast_origin` (T0), `earliest_fill` | the three clocks of contract section 4 | forecast and settlement | derived |
| `discovery_at`, `manifest_sealed_at`, `snapshot_at` | the cycle sequence of contract section 6 | scheduler | host clock |
| `known_at` | when a fact became available to the system, for point-in-time reads | evidence | `received_at` |

Rules (each is a test): `event_time` may be earlier or later than `received_at` and is never used to decide what the system knew; a read "as of D" returns only records with `known_at <= D` (Qlib's point-in-time rule: each row carries its publication date and a revision appends a new row; ledger entry A06); a record's `committed_at` is monotonic non-decreasing in ledger order; the host clock is disciplined by NTP and its offset against two reference servers is measured at start-up and every hour (measured today: +0.023 ± 0.106 s against `time.apple.com`); the ledger writer refuses to commit when the measured offset exceeds 1 second, and the refusal is itself a ledger event.

## 3. Identifiers

- **Natural-key ids make replay idempotent, and an id names exactly one thing.** `id = prefix + base32(SHA-256(natural key))[:26]`, with the natural key stated per type: `cp` (a capture, one network fetch) over `(gateway instance, tool, canonical request, gateway sequence number, received_at)`, assigned and stored in the outbox row **before** the append so that re-delivery of the same outbox row reuses it; `ev` (an evidence observation) over `(capture id, adapter version, item path)`; `fc` (forecast) over `(cycle_id, arm_id, asset_id, contract, horizon)`; `dc` (decision) over `(cycle_id, arm_id, candidate_id)`; `st` (settlement) over `(forecast_id, oracle_pair, settlement_rule_version, revision)` where `revision` is 0 for the first settlement and a correction is a new record with the next revision that `supersedes` the old. **The content hash is an attribute, not an identity:** the first version of this section keyed evidence on `(tool, canonical request, raw_sha256)`, which gave two fetches of identical bytes at different times one id, so the second observation was dropped or rejected as a conflict (E19: an observation was lost in 1,934 of 2,000 simulated sequences and a point-in-time read was wrong in 1,004; with the capture-based scheme, 0 and 0). Identical bytes fetched twice are two captures and two observations that share one stored blob (content-addressed, stored once); freshness and `known_at` come from the observation's `received_at`. Producing the same record twice yields the same id; the ledger treats an equal-content duplicate as a no-op and a different-content duplicate as a conflict that is rejected and counted (CloudEvents' `source` + `id` rule, adapted).
- **Random ids** (UUID version 4) only for things with no natural key: `Identity`, `Investigation`, `Contribution`.
- **`asset_id`, quote class and instrument kind** are carried unchanged on every record that mentions an asset, from the claim node to the settlement (contract section 39). A mismatch at any link is `IDENTITY_MISMATCH`.
- **`source` is a producer name** (`urn:hoshitori:gateway:ryo`, `urn:hoshitori:agent:L2:v7`), and `(source, id)` is the dedupe key.

## 4. Record envelope (the unit of the ledger)

Every ledger record is one canonical JSON object (record profile, `../experiments/E1`):

```
{ "spec": "hoshitori.record/1",
  "type": "evidence" | "claim" | "hypothesis" | "decision" | "challenge" | "commitment" | "outcome" | ...,
  "type_version": 1,
  "id": "<prefix><26 base32>",
  "source": "urn:...",
  "created_at": "2026-10-27T03:10:00.123Z",
  "body": { ... type specific ... },
  "refs": [ {"id": "...", "sha256": "<hex of the canonical bytes of the referenced record as its producer submitted it: the stored record without committed_at (CF-49)>"} ],
  "privacy": "public" | "private_digest_only" }
```

- **Canonical bytes** are the RFC 8785 canonical form under the record profile: strings, booleans, `null`, arrays, objects, integers within ±(2^53 − 1); every price, size, probability and ratio is a **decimal string**; text is NFC; no duplicate keys; depth ≤ 64. The ledger stores exactly these bytes, and the leaf hash is SHA-256(0x00 ‖ bytes) (RFC 9162).
- **Schemas come from one place.** Each `body` has a Pydantic v2 model (`extra="forbid"` on write, strict types), from which the JSON Schema, the OpenAPI 3.1 document (FastAPI emits 3.1.0) and the TypeScript types are generated; no hand-written second schema exists. Readers ignore unknown members (I-JSON must-ignore) but the verifier hashes the exact bytes.
- **Evolution.** `type_version` increments on any change of meaning; an upcaster maps old to new at read time; old records are never rewritten. A record whose `type_version` the reader does not know is shown as `UNREADABLE_VERSION` and is not interpreted.
- **References carry hashes**, so a record cannot be silently swapped for another with the same id (the E2c record-tamper case).

## 5. Entities

The seven named records and the objects around them. Fields are the contract-bearing ones; the full Pydantic definitions belong to the build.

| Entity | Authoritative fields | Owner | Lifecycle |
|---|---|---|---|
| `RawCapture` | `capture_id`, `source`, `request` (method, url or tool, arguments), `status`, `headers` (secret-free), `body_sha256`, `body` (stored bytes, private by default), `t_send`, `received_at`, `trace_id` | Gateway | write-once |
| `Asset` | `asset_id`, `kind` (spot, perpetual, future), `base`, `quote`, `quote_class`, `venue`, `instrument_id`, `chain_id`, `contract_address`, `status` (`RESOLVED`, `AMBIGUOUS`, `UNKNOWN`), `resolution_evidence` | Evidence | versioned by `valid_from` |
| `EvidenceItem` | `evidence_id`, `capture_id`, `raw_sha256` (the content, shared by identical fetches), `tool_or_source`, `request_normalised`, `status`, `data_mode`, `availability`, `fields` (decimal strings; unknown stays absent with a reason), `event_time`, `first_seen_at`, `received_at`, `known_at`, `origin_family`, `derived_from[]`, `transform_version` | Evidence | write-once; a revision is a new item that `supersedes` the old |
| `Claim` / `ClaimGraph` | node `type`, `source_span` (immutable), `user_reading` (separate), `asset_id`, `status`, edges (`depends_on`, `causal`) | Claims | nodes immutable; status changes are new `SupportCheck` records |
| `Hypothesis` | statement, `claim_nodes[]`, `invalidation[]`, `triage_rule_version` | Research | write-once; a change is a `Revision` |
| `Decision` | `decision_id`, `cycle_id`, `arm_id`, `candidate_id`, `action`, `proposal` (model) and `final_action` (after gates), `gate_events[]`, `evidence_ids[]`, `observation_set_hash`, `model_outputs_digest` | Research | write-once |
| `Forecast` | contract fields of contract section 1 (decimal strings) | Forecast | state machine of contract section 17 |
| `Challenge` | `mode` (as-of audit, new evidence, hypothetical), `intervention`, `controls`, `n`, `result_label`, `change_mechanism` | Research | write-once; original decision stays locked |
| `Commitment` | `commitment_id`, `checkpoint` (origin, size, root), `tokens[]` (operator, signer fingerprint, `genTime`, accuracy claimed, digest algorithm), `level`, `assurance_class` | Ledger | levels only increase; a downgrade is a new record |
| `Outcome` / `Settlement` | `forecast_id`, oracle pair, settled event, score inputs, state (`SETTLED`, `VOID(reason)`, `UNSETTLEABLE(reason)`) | Forecast | write-once; a correction is a new linked record |
| `Cycle`, `Manifest`, `Snapshot` | contract sections 6 and 25 | Forecast | `PLANNED, COLLECTING, SEALED, SNAPSHOT_FROZEN, RUNNING, COMMITTED, ABANDONED` |

## 6. The ledger as the single write path

- **One writer.** A single ledger-writer process appends records; all other components submit records to it through the outbox (section 7). This is what makes SQLite's single-writer limit a design property rather than a risk (ADR-001, experiment E3).
- **Storage.** Canonical record bytes in append-only segment files (JSON Lines, one canonical record per line, with the leaf hash), plus the SQLite database for indexes and operational state. The SQLite database is rebuildable from the segments (test, section 9); the segments are rebuildable from nothing, so they are the object that is backed up, replicated and checkpointed.
- **Checkpoints.** After each batch the writer computes the RFC 9162 tree head over the whole log, signs a C2SP checkpoint with the log's Ed25519 key, and stores it. A forecast batch also asks the time-stamp authorities for tokens over the checkpoint (verification spec).
- **No overwrite.** The only operation is append. A correction is a new record that references the old.

## 7. Atomic hand-off, recovery and idempotency

The ledger has two durable places that cannot share a transaction: the **segment file** (the source of truth for order and content) and the SQLite database (the outbox and a rebuildable index). The protocol is built for the gap between them. Review 20 found the first version of this section wrong in two ways: it said to repeat "append, index, mark done" for every outbox row without an index row, which appends a second copy of a record that was written just before a crash; and its line "crash points tested" rested on E3, a SQLite-only test whose "ledger" was a table, so the segment-plus-index gap had never been exercised. Both are corrected and measured (E20).

1. **Producer**, in one `BEGIN IMMEDIATE` transaction: writes its own state row and an `outbox` row holding the canonical record and its natural id.
2. **Recovery, on every start, before any append:** scan the segment from the last indexed offset. A torn last line (no newline, unparseable, wrong leaf hash or wrong sequence number) is truncated. Index rows that point beyond the valid end of the segment are deleted and their outbox rows reopened. Every valid line missing from the index is indexed and its outbox row marked done. Nothing is appended during recovery.
3. **Append:** for each pending outbox row that is not in the index, write one line (sequence number, id, leaf hash, canonical record), fsync, then in one transaction insert the index row and mark the outbox row done.
4. **Remote effects** (time-stamp requests, witness cosignatures, public-log submissions) go through the gateway as an attestation request (gateway design) and are **at-least-once with deduplication by checkpoint digest**: a second token for the same digest is harmless and is stored; nothing claims remote exactly-once.

**Measured (E20: real process kills with `os._exit` at each point, 60 rounds per point and protocol, macOS APFS):** with the first-written protocol a kill in the middle of a line, after the write and before the fsync, or after the fsync and before the index row left a violation (a torn tail never repaired, or a duplicated line) in 180 of 180 rounds, and none before the append or after the index commit; with the reconciling protocol 0 of 300 rounds violated any invariant (no duplicate line, gapless sequence, valid leaf hashes, index equal to a rebuild from the segment, outbox empty). File-level simulations on top of a clean run (the segment tail cut while its index rows remain; the last index commits lost; a byte flipped in the last line): 0 violations and no lost record in 180 rounds. **Not shown:** real power-loss durability (a process kill leaves the operating system's cache intact), the target host's filesystem, concurrent readers during recovery.

5. **Pending commands (review 21, ADR-027, E22).** Acknowledging a command after only the SQLite commit lost 199 of 2,994 acknowledged commands silently when SQLite was restored from an older backup. A producer's acknowledgement therefore follows an append to the intent journal (fsynced, replicated with the segments) and the outbox row; recovery replays journal intents found in neither the ledger nor the outbox; a producer receipt that matches nothing becomes a FAILED_LOST record naming the receipt and the reason. A restore that makes a committed record disappear is a different case, handled by the attested-prefix check (ledger spec invariant 9).
6. **Recovery against attested checkpoints (review 21, ADR-026, E20b).** After step 2 the writer compares the segment with the highest externally attested checkpoint; a mismatch is repaired from a replica or stops the log in INTEGRITY_FAILURE. The outbox is never treated as a copy of history (E20b: it hid the defect while it kept finished rows).

Crash points covered: before the append; in the middle of a line; after the write and before the fsync; after the fsync and before the index row; after the index commit; and, as file states, a segment shorter than its index, an index shorter than its segment and a corrupted last line. Not yet covered by a test: between checkpoint signing and the time-stamp request, and between the request and storing the token (specified only).

## 8. Trust boundaries

| Boundary | Untrusted side | Enforced by |
|---|---|---|
| Verifier input | a bundle that may carry its own policy, keys and certificates | the verifier uses only a trusted root supplied from outside the bundle; without one its verdict says the bundle supplied its own trust anchors and nothing about origin (verification spec) |
| External responses | RYO, exchanges, news, social, URLs | Gateway captures bytes; Evidence parses with strict typed adapters; nothing from an external response is executed or used as an instruction (contract section 53) |
| Model output | any LLM text | typed output schema; deterministic gates (grounding, required evidence, risk kernel) own the final action; the model never writes a record |
| Browser | identities, challenges, claim text | signed requests with nonce and expiry; private by default; per-artifact publish preview |
| Third-party code | libraries, rival repositories | pinned versions, licence register, never run outside an empty environment in study |
| The operator (us) | our own database and key | independent external commitment (time-stamp tokens, witnesses) so that a full rewrite by the operator is detected (E2c case "full operator rewrite") |

## 9. Failure modes, tests and the journey

| Failure | Required behaviour | Test |
|---|---|---|
| Duplicate delivery | no-op | replay the outbox twice; ledger length unchanged |
| Same id, different content | rejected, counted | conflict case |
| Unknown `type_version` | shown unreadable, not guessed | fixture |
| Derived store lost | rebuilt from segments and captures; byte-identical derived rows | rebuild test |
| Clock offset above 1 s | commits refused, event recorded | fault injection |
| Identity mismatch | `IDENTITY_MISMATCH`, excluded and counted | whole-journey identity test |
| Private payload deleted | ledger keeps the digest; reconstruction reports `NOT RECONSTRUCTIBLE` | deletion test |

**Journey (one claim, one investigation, one evidence challenge, one independently timed record, one honest outcome):** claim text becomes `Claim` nodes (immutable spans); `Check`s run through the gateway and become `EvidenceItem`s; the `Decision` cites evidence ids with hashes; a `Challenge` is a new linked record; the `Forecast` is committed in a batch whose checkpoint carries independent tokens; the `Settlement` references the forecast and the oracle pair. Each arrow is a hash reference, so the bundle for the forecast contains exactly the chain a stranger can verify offline.
