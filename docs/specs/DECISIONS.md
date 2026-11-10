# Architecture decision records

Each record states the context, the alternatives considered (including doing nothing), the decision, the evidence and its limits, the invariant it protects and the test that would falsify it. A decision is OPEN when the evidence needed does not exist yet; it is never presented as settled. Status words: DECIDED, DECIDED (limits), OPEN, BLOCKED (needs a key or a person).

| ADR | Title | Status |
|---|---|---|
| 001 | Operational store and migration trigger | DECIDED (limits) |
| 002 | Canonical record profile | DECIDED |
| 003 | Own transparency log in C2SP formats | DECIDED (limits) |
| 004 | Time-stamp operators, independence and policy | DECIDED (limits) |
| 005 | RYO client | DECIDED (limits); the metered probe of limits and concurrency is not done |
| 006 | Orchestration | DECIDED |
| 007 | Ledger layout | DECIDED |
| 008 | Evaluation harness | DECIDED (limits) |
| 009 | Model provider | DECIDED (limits): benchmark run, forecast skill NOT MEASURED |
| 010 | Web stack and rendering | DECIDED (limits) |
| 011 | Accessibility structure | DECIDED (limits) |
| 012 | Backup and restore | DECIDED (limits) |
| 013 | Interval procedure for league comparisons | DECIDED (limits) |
| 014 | Statistics of evidence challenges | DECIDED |
| 015 | Falsification test library | DECIDED (limits) |
| 016 | Monitoring rule for promotion of lessons | DECIDED (limits) |
| 017 | Author-signed provenance for social records | DECIDED |
| 018 | Whale collector design | DECIDED (limits) |
| 019 | Executable lifecycle model | DECIDED |
| 020 | Trusted root outside the bundle | DECIDED (limits) |
| 021 | Observation identity separate from content identity | DECIDED |
| 022 | Ledger recovery: the segment is the source of truth | DECIDED (limits) |
| 023 | One network egress with an attestation class | DECIDED |
| 024 | Operator registry with ownership evidence; C2 is a claim | DECIDED (registry content open) |
| 025 | Fenced promotion: epochs, continuity proof, published-chain acceptance | DECIDED (limits) |
| 026 | Recovery respects externally attested checkpoints | DECIDED (limits) |
| 027 | Intent journal and producer receipts for pending commands | DECIDED (limits) |
| 028 | Trusted-root lifecycle from the TUF root-update rules | DECIDED (limits) |
| 029 | Structured ownership evidence: public CA database and operator-published roots | DECIDED (content partly open) |
| 030 | Whole-product integration proof: fixture harness first, genuine journeys later | DECIDED (genuine proof blocked) |

## ADR-001 Operational store and migration trigger

Context: one writer, small load, rebuildable from the ledger. Alternatives: Postgres from the start; SQLite WAL; a document store; nothing separate from the ledger files. Decision: SQLite in WAL mode with `BEGIN IMMEDIATE`, one writer process, short reads, a checkpoint job and a WAL-size alarm. Evidence: E3, about 5,300 durable commits per second on one core (process-kill durability only); a reader held for 3 s grew the WAL to 1.37 GB with 9 of 333,357 frames checkpointed; E12, a 410 MB database backed up online in 10.9 s with the writer's p99 commit latency moving from 0.05 to 0.47 ms. Migration trigger (priors, to confirm on the target host): sustained write rate above 500 commits per second, writer p99 above 50 ms, a second writer process, or a second host needing the same database; the ledger segments do not migrate, only the index. Invariant: L-7 (index rebuildable). Falsifier: the 24-hour soak on the target host at ten times the expected rate. Limits: macOS durability is not power-loss durability; the target filesystem is unverified.

## ADR-002 Canonical record profile

Decision: RFC 8785 restricted to an I-JSON subset (top-level object; integers within ±(2^53 − 1) only; decimal strings for quantities; NFC strings; depth at most 64; duplicate keys, lone surrogates, a byte-order mark and non-finite numbers rejected); raw third-party JSON is hashed as received. Alternatives: plain JSON.stringify and sorted keys; CBOR or DRISL; protobuf. Evidence: E1, 146 vectors with 0 own failures, 12 deviations by two third-party canonicalisation libraries (the Python one raises on integers beyond 2^53 − 1 that the RFC serialises as doubles; neither is a verifier because both accept duplicate keys when fed by their language's ordinary parser), Python and JavaScript agree on 10,000 mutated inputs; Node 25.5.0's `JSON.parse` corrupted keys in 3 of 4 seeds (CF-12). Invariant L-5. Falsifier: any disagreement between the two implementations on a generated input.

## ADR-003 Own transparency log in C2SP formats

See `TRANSPARENCY_LOG_SPEC.md` section 1. Alternatives: Tessera; Rekor v2 only; a blockchain anchor only. Decision: own small log, C2SP checkpoint and proof formats, independent time from RFC 3161 tokens, independence from witnesses and a public log we do not control. Falsifier: a C2SP-conformant third-party verifier failing a bundle of ours.

## ADR-004 Time-stamp operators, independence and policy

Decision: two or more operators counted by (root fingerprint, signer fingerprint); a token counts only within `[committed_at − 1 s, committed_at + 60 s]`; SHA-1 CMS signers rejected; the policy file lists accepted roots. Evidence: E2b (nine authorities, 903 hostile token cases with 0 verifier disagreements; two endpoint pairs share signers), E2c, E16 (CF-22, CF-23). Limits: no authority is TA-1; revocation evidence is archived at stamping and not yet implemented; both authorities honest at once is assumed.

## ADR-005 RYO client

Decision: REST as the primary path (the server speaks MCP 2024-11-05 with text-only errors); the client is built from the authenticated `/tools` catalogue; one gateway is the only caller; one retry layer; both rate models (fixed window and token bucket) are budgeted until `--measure-limit` decides. Alternatives: MCP client first; a fixed tool list. At the time of writing there was no key, so the catalogue, shapes, status semantics and limits were unverified; the first authenticated read since (six tools, four answered live and two answered 503) is recorded in the build records, and the limits, validation errors and concurrency remain unmeasured. Falsifier: the metered probe.

## ADR-006 Orchestration

Decision: a thin typed state machine whose durable state is the ledger plus the outbox. Alternatives: LangGraph (checkpointing, but replay semantics host-dependent in issues read), Temporal (heavy), DBOS (measured). Evidence: E4, DBOS on SQLite survived 30 SIGKILL rounds over 10,814 workflows with no duplicate ledger line, at 383 workflows per second against 1,830 for the plain outbox, with one duplicated external done-mark: durable execution is at-least-once for external effects, so idempotency keys are required either way. Falsifier: a kill round that duplicates or loses a record.

## ADR-007 Ledger layout

Decision: append-only JSONL segments (closed segments immutable and hashed) plus a rebuildable SQLite index. Alternatives: a single SQLite table as the ledger; Tessera tiles. Evidence: ledger spec section 4, E3. Falsifier: any index rebuild that is not byte-identical.

## ADR-008 Evaluation harness

Decision and evidence in `AGENT_PROTOCOL.md` (spike E15): Inspect AI for model-in-the-loop benchmark runs behind a mandatory coverage report; league settlement is our own deterministic scorer. Limits: mock model only; real provider behaviour, caching and rate-limit handling unverified.

## ADR-009 Model provider

Decision: `nvidia/nemotron-3.5-super-vl-120b-a12b` on the NVIDIA OpenAI-compatible endpoint with thinking off (`enable_thinking: false` in `chat_template_kwargs`), behind the `ModelClient` interface, so the choice can be replaced by a settings file.

Evidence (the stored snapshot of a real cycle, seven assets, horizons 1 h and 4 h, three repeats, 42 single judgments per setting; raw captures kept outside the repository). Round 1 (prompts v2) over three models, each with thinking off and on: only the 120b model was usable; the 550b model timed out or was invalid most of the time, and the small model gave a valid first answer in about a fifth of the cases. Round 3 (prompts v4, the 120b model): thinking off gave a valid first answer in 42 of 42, all 42 became calls, median 5.5 s, 95th percentile 38 s, about 3.1k billed tokens, the same verdict on every repeat; thinking on gave 5 calls, 5 abstentions and 32 failures (truncation at the 12k-token cap and unavailable replies), median 60 s. So thinking off is chosen by the numbers; the same runs found the three defects of CF-63 and CF-64, which were fixed before round 3.

Limits, stated plainly: the probabilities cluster between 0.48 and 0.55, so the arm is cautious and its skill is NOT MEASURED (no outcome has settled for these forecasts); 42 judgments on one snapshot is a reliability and latency measurement, not a quality one; the debate (judge) arm was run on four cases only; the endpoint's availability varied between rounds (thinking on lost 14 replies as unavailable in round 3); retention and training terms of the endpoint are NOT VERIFIED (not read). Falsifier: forward settled outcomes of the model arms against the rules arm and the matched-compute control.

## ADR-010 Web stack and rendering

Decision: React and TypeScript with Vite, React Aria Components, Today served from a precomputed document and rendered statically; progressive enhancement. Evidence: E11 (a mock built from the card contract passes axe with zero violations and zero incomplete results, with visible focus and 44 px targets, at three widths and two schemes; the contract's example copy missed the first-viewport word targets, and copy written to the budget met them: CF-17, CF-18). Limits: one page; no performance budget measured; no screen reader run.

Amendment (Stage 7, built): React 19.3.1 and TypeScript 6.0.3 with Vite 8.3.4, each chosen at least two weeks after its release and pinned exactly; React Aria Components is not used yet because the first pages (a table, links, headings, a skip link) need no primitive with difficult behaviour, and a library is a dependency to disclose and test: it joins when the Desk needs dialogs, comboboxes or disclosure widgets. System fonts only (no web font blocks text, and none needs a licence line). The pure modules are tested with Node's own runner (Vitest 5 declares Node 22, 24 and 26 and this machine has 25). The page test runs in Chromium, Firefox and WebKit (Playwright) with axe-core 4.10.2, a keyboard walk and screenshots that are read by a person. Result: 30 page states per engine, no axe violation, no console error, no overflow, a visible focus ring on every tab stop, the skip link first. Not measured: a screen reader, comprehension, the JavaScript budget before interaction (the whole bundle is 74 KB gzipped).

## ADR-011 Accessibility structure

Decision: lists of cards (not an ARIA grid), a native table with sortable header buttons for the Screener, charts as text plus table plus marker list. Evidence: WCAG 2.2 text and the APG grid and table patterns read; E11 tab walk. Falsifier: the manual protocol with a daily screen-reader user.

## ADR-012 Backup and restore

Decision: closed segments replicated; the operational SQLite backed up with the online backup API; never a file copy of a live WAL database; Litestream not adopted. Evidence: E12b, 35 of 60 plain main-file copies failed to open and 5 more failed the integrity check; E12c, 60 of 60 online backups consistent. Falsifier: a restore drill failure.

## ADR-013 Interval procedure for league comparisons

Decision (open for amendment by a later study: the equal-weighted cosine estimator covered better in a re-run): Newey-West t interval on daily mean paired differences with the matching-simulation coverage printed beside it; a procedure may be shown only if its simulated coverage is within 0.03 of nominal. Evidence: E6, E6b (the planned 5-day block bootstrap covered 0.82 to 0.85 for nominal 0.90; nothing reached nominal under strong autocorrelation). Limits: synthetic data; strong autocorrelation remains uncovered, in which case the page says "approximate".

## ADR-014 Statistics of evidence challenges

Decision (amended by a later study: Tango's or an exact paired interval replaces the Newcombe form, which is zero-width with no discordant pairs): Newcombe paired interval and exact McNemar test; labels by measured widths (CF-15, CF-16): `DECISION_STABLE` needs at least 60 pairs (40 at concordance 0.8); a 5-run result can never support `DECISION_CHANGED`. Evidence: E8.

## ADR-015 Falsification test library

Decision: `T_MARKET`, `T_ONSET` (one-look rule), `T_PLACEBO_TIME`, with each test gated by a planted-data calibration (false-alternative rate at most 10 percent on planted true effects). Evidence: E7 (the first onset rule false-alarmed in 25 and 27 percent of cases). Limits: synthetic data only.

## ADR-016 Monitoring rule for promotion of lessons

Decision: always-valid mixture sequential monitoring, permitted only to stop for harm or futility; promotion at the fixed endpoint. Evidence: E10 (a p-value checked after every observation crossed 0.05 in 56 percent of null streams; the always-valid p-value in 3.05 percent). Limits: independence assumed in the simulation; autocorrelated day effects require the same matching calibration (E6) before use.

## ADR-017 Author-signed provenance for social records

Decision: store the repository proof (CAR) of every Bluesky record and verify authorship offline; provenance level SP-0. Evidence: E14, 18 of 18 live posts verified and 70 of 70 negatives rejected. Limits: authorship, not time or liveness; key rotation.

## ADR-018 Whale collector design

Decision: stream at most 10 priority wallets, poll the rest under a published weight budget, cache roles. Evidence: venue limits read (CF-21). Limits: the budget is arithmetic from the published weights, not measured.

## ADR-019 Executable lifecycle model

Decision: keep the model of E16 as a living specification; the real implementation must pass the same property test through an adapter; invariants without a model are listed as not yet tested. Evidence: 8 of 8 injected faults caught; two contract holes found. Falsifier: any implementation run that violates an invariant of `INVARIANTS.md`.

## ADR-020 Trusted root outside the bundle

Context: the first verifier trusted the policy inside the bundle (an outside review). Alternatives: pin only the log key; a trust-on-first-use cache; a trusted root shipped with the verifier. Decision: a trusted root (log key eras, pinned time-stamp roots, operator registry, allow-lists, witness set, each with a validity range) shipped with the verifier release and published through independent channels; the bundle policy is metadata; without a root the verdict is `BUNDLE_ASSERTED`. Evidence: E2c (whole-bundle substitution and an offline forgery with self-made authorities pass without a root, fail with one, in two languages); Sigstore's trusted-root design (a separate document with validity ranges; the proto read, the client documentation not). Limits: succession by signed updates and the distribution channels are designed, not built; a user who runs the verifier without a root gets a self-consistency verdict only. Falsifier: any bundle that reaches `C3` or `scorable` without the pinned root.

## ADR-021 Observation identity separate from content identity

Decision: capture id per fetch (assigned and stored before the append, reused on re-delivery), evidence id over capture, adapter version and item path, content hash as an attribute with one stored blob; settlement ids gain a revision. Evidence: E19. Falsifier: two identical fetches at different times that produce fewer than two observations.

## ADR-022 Ledger recovery: the segment is the source of truth

Decision: recovery runs first on every start (truncate a torn tail, index valid unindexed lines, remove index rows beyond the segment and reopen their outbox rows) and nothing is appended during it; append only outbox rows not already in the index. Alternative rejected: repeat the append for every outbox row without an index row (failed 180 of 180 at three crash points). Evidence: E20. Limits: process-kill and file-level simulations on macOS; real power loss and the target filesystem are NOT VERIFIED. Falsifier: the E20 rounds on the target host with a real power-cut rig or a block-device crash tool.

## ADR-023 One network egress with an attestation class

Decision: the gateway makes every outbound connection, including time-stamp, witness and public-log requests (class `PA`, handed over through the outbox); the ledger writer and the web process have no egress; tests attempt connections from each. Falsifier: any allowed destination from a non-gateway process.

## ADR-024 Operator registry with ownership evidence; C2 is a claim

Decision: operators (time-stamp authorities, witnesses, public logs) are entries with a parent organisation and ownership evidence in the trusted root; independence needs distinct parents and evidence; public-commit references are claims offline. Evidence: E2c cases. Limits (updated after review 21): the first version accepted any non-empty string as evidence; ADR-029 makes it structured and E23 supplies real entries for five of seven operators; SwissSign and FreeTSA have none, and an entry without evidence counts for nothing, which is the conservative failure.

## ADR-025 Fenced promotion: epochs, continuity proof, published-chain acceptance

Decision: failover is a manual, fenced act, never an automatic election. (1) A single active writer exists per epoch; an epoch is taken by a compare-and-swap in a linearizable store, and a candidate that read an older epoch loses. (2) The candidate must prove continuity: its log must contain the last published checkpoint (size and root) of the previous epoch, shown with an RFC 9162 consistency proof; if it cannot, it fails closed. (3) The epoch change is an epoch record in the log that fixes the closing size of the previous epoch's key; each epoch has its own signing key registered in the trusted root. (4) A checkpoint is released (published, time-stamped, handed to witnesses) only through a compare-and-swap that carries the writer's epoch and the previous pointer. (5) A checkpoint is accepted by a verifier only if it is consistent with every published checkpoint and, when signed under an epoch's key, its size does not exceed that epoch's closing size. Evidence: E21 (1,500 schedules per protocol): the file-lock design produced inconsistent published checkpoints in 1,103 and a second writer in 719 simultaneous promotions; fenced promotion produced 0 forks among published or accepted checkpoints, refused 507 promotions that could not prove continuity and 647 simultaneous second promotions, and rejected 3,503 checkpoints signed by a stale primary. Limits: the store is assumed linearizable (no provider chosen; conditional-write guarantees NOT VERIFIED per provider); the model has no packet-level network; a stale primary can still sign locally, which is why acceptance is a rule of the verifier and not of the writer. Alternatives considered: automatic election with a consensus library (adds a distributed system the plan does not otherwise need; a single manual promotion is rare and a stalled log is a visible, safe state).

## ADR-026 Recovery respects externally attested checkpoints

Decision: after reconciling the local tail (ADR-022), recovery compares the segment with the highest externally attested checkpoint it knows (time-stamp tokens, witness cosignatures, the replica's manifest). A mismatch is repaired only by restoring the prefix from a replica whose Merkle root equals the attested root; otherwise the log enters INTEGRITY_FAILURE, which is visible on every surface, and no record is appended. Recovery never resumes over a prefix it cannot prove. Evidence: E20b: reconciling-only recovery resumed over a non-intact attested prefix in 500 of 600 rounds with the outbox pruned and 200 of 600 with it kept (the rest were repaired only because the outbox still held the records); attested recovery 0 of 600, failing closed in 500 and restoring from a replica in 500 of 500. Limits: file-level faults; real power loss not shown; depends on at least one attestation stored off the host.

## ADR-027 Intent journal and producer receipts for pending commands

Decision: a command is acknowledged to its producer only after its intent is appended to a separate journal and fsynced, in addition to the outbox row; the journal is replicated like the segments; recovery replays intents that are in neither the ledger nor the outbox; the producer holds a receipt id, and a reconciliation turns any acknowledged id found nowhere into a FAILED_LOST ledger record with its reason, so the loss is counted, never silent. In-flight remote requests are idempotent by digest. Evidence: E22: SQLite-only acknowledgement lost 199 of 2,994 acknowledged commands silently (81 of 150 rounds); journal alone fixed the backup restore and not a lost journal tail or a replaced host; journal plus receipts left 0 silent omissions and accounted 199 and 207 as FAILED_LOST. Limits: the producer must keep its receipts outside the SQLite that failed; for the scheduler's own commands the cycle identity is deterministic and the cycle is regenerated, not replayed.

## ADR-028 Trusted-root lifecycle from the TUF root-update rules

Decision: the trusted root is a chain of versioned documents. Version N+1 is accepted only if it carries valid signatures from a threshold of version N's root keys and a threshold of its own, each key counted once; its version is exactly N+1; only the final root's expiry is checked, against a time fixed at the start of the update; the chain length is bounded; the documents come from at least two independent channels and two different documents for one version raise FORK; the verifier release pins its root. A root document also carries the log-key eras with their closing sizes, the pinned time-stamp roots, the operator registry, the accepted evidence kinds and the algorithm allow-lists. Evidence: TUF specification section 5.3 read in full; E24: 23 cases in two languages, 0 disagreements, six planted mutations caught. Limits: a verifier that cannot reach a newer root keeps accepting a retired era until its own root expires, so the expiry period (a prior of one year) is the freeze bound; which channels (release artifact, a repository with signed tags, witness-hosted copies) is a decision for the project owner; the signing ceremony and key custody are not designed.

## ADR-029 Structured ownership evidence: public CA database and operator-published roots

Decision: the operator registry accepts only structured evidence of a kind listed in the trusted root: `ccadb_ca_owner_of_record` (the pinned root's SHA-256 has a record in the public CA database, whose owner and record id are stored) and `operator_published_root` (the operator's own HTTPS site publishes that certificate, and the SHA-256 of the download equals the pin). The evidence must be about the very root a token chained to. Registry keys are pinned roots; endpoint labels are not operators. Evidence: E23 and E2c (48 cases): DigiCert, Sectigo, GlobalSign and Certum from the database, Apple from its own site; two real operators reach C3 in both verifiers. Limits: organisation of record, not ultimate owner (NOT VERIFIED); SwissSign and FreeTSA have no evidence; the database is a public third-party register whose reuse terms for redistribution are not read.

## ADR-030 Whole-product integration proof: fixture harness first, genuine journeys later

Decision: the integration claim is made as seven properties checked across every connection (same truth, authority, history, failure, privacy, outcome, reasoning), each with a mutant stage built to break exactly that property. A fixture-level harness exists now (E25: one labelled synthetic fixture through capture, evidence, decision, canonical record, log with inclusion proof, offline check, view model and settlement; honest pipeline passes all seven properties in a normal, an unavailable-source and a private-input run; 9 of 9 mutants caught). The genuine proof (real RYO responses, real model calls, crashes, delayed acknowledgements, stale replicas, failed time-stamp services, independent attackers) is specified in `INTEGRATION_PROOF.md` and blocked on the RYO key, a model credential and an independent reviewer. Limits: the harness shows the method and catches planted breaks; it shows nothing about real data, real models, real storage or real users.



### Amendment to ADR-025 (a later study)

The promotion step is itself a compare-and-swap over the checkpoint pointer as well as the epoch, so the closing size of the old epoch is the size at the moment of the switch. A split-step model (read pointer and prove continuity, the old primary publishes once more, then the epoch changes) lost an honestly published checkpoint when the swap carried the epoch only, and failed safely when it carried both. Tests: the two scenarios of the failover checks (`tests/failover_checks.py`).

## ADR-031 The Desk investigates a plain disagreement between agents (decision: yes, in two stages)

Context: the Desk starts a run only from an event the league admitted that then moved; in the whole record no event has done so, so the Desk
has never committed a call of its own (README "What is real"). The strongest rivals show a visible thinking moment; ours is invisible for
that reason. Disagreement between agents on one call is frequent (115 of 525 up-or-down slots in the packed record had one side at most 49
and the other at least 51, six points or more apart; read from `rows.json`).

Decision: yes. A disagreement that meets the page's rule (opposite sides of 50,
at least six points apart, same token and horizon, same cycle) is a Desk trigger on a new route-neutral template, under the same cap (three
runs a day, one per token a day, once per trigger, at most 3 model calls and 1 RYO call). Two stages, because the second needs calibration:

1. **Stage 1.** A new template pair in the closed list (`SIDE_UP`: "The agents disagree on SOL over 4 hours. The up reading holds."; the
   Desk is long-only, so a down reading is a flat hypothesis as for a move down). The trigger carries no event time, so the existing
   `without_event_time` path applies: the onset and market tests are NOT_TESTABLE, the source and placebo tests run, and the confidence
   ceiling is capped as for any run whose time-anchored tests could not be done. The run is honest about that: most such runs will end flat or
   abstain, and the receipt says which tests could not be run and why. The value is the visible reasoning on both sides, not a profit.
2. **Stage 2.** Anchor the market test on the cycle's snapshot time and the horizon instead of an event time (does the token's move over the
   window exceed what the market explains?), calibrated on planted effects like the other tests before it may lift the ceiling.

Consequences: the contract's closed list of templates (section 57) grows by two; `desk_plan` and `desk_hook` change (swept: re-sweep);
the product description states that a disagreement can start a run. Rejected: letting a disagreement lift the confidence ceiling before stage 2
is calibrated (it would claim a test that did not run); starting a run on every disagreement (the cap stays).

Amendment to ADR-031 (stage 2 re-scoped): anchoring the market test on the cycle's time would test the move that has already happened, not the
up reading of the future the agents disagree about, so it would be a test of the wrong assumption. Stage 2 is therefore a search for tests that
bear on the up reading itself (for example whether the funding sign or the other venue's momentum has predicted the next window on history),
each calibrated on planted effects before it may lift the ceiling. Until one passes, a run on a disagreement ends flat or abstains and says so.

