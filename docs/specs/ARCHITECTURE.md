# Architecture

Scope: the whole, its boundaries and its flows. Status: DESIGNED. This document was written as a design before the build and is kept as that design; most of it has been built since, and the unit-by-unit state, each unit with its evidence and its limit, is `../STATUS.md`. It also reports a set of measured spikes (E1 to E<!--n:exp-->25<!--/n-->). Product rules: `FORECAST_SETTLEMENT_CONTRACT.md` (the authority for them); this document says how those rules are made true by structure, and what remains uncertain.

## 1. One-paragraph shape

A **gateway** is the only process that talks to the outside. It writes **raw captures**. **Evidence** parses them into typed, hashed, time-stamped items with origin families and asset identity. **Claims** turns text into a claim graph and checks it against the evidence. **Research** runs investigations, picks the next check with one shared function and produces decisions; the **league** runs four controlled arms on frozen snapshots in cycles; **Forecast and settlement** turns decisions into committed forecasts, practice books and deterministic settlements. Everything authoritative goes through **one ledger writer** into append-only segments with signed checkpoints, independently time-stamped; **Verification** is a pure offline function over bundles. **Presentation** (rankings, view models, pages) is derived and disposable. **Social** adds identities, contributions and arenas, never touching a forecast or an outcome.

## 2. Processes and deployment

| Process | Role | Scale |
|---|---|---|
| web/API | serves static pages and the HTTP interface; submits to the outbox | one, stateless apart from the database |
| scheduler | cycle state machine, heartbeat, discovery and manifest, snapshot, arms, commit | one active per epoch; a file lock only inside one host; a replica is promoted only by the fenced procedure (ADR-025) |
| gateway | the only network egress: priority queue, budgets, circuit breakers, safe client, including time-stamp, witness and public-log requests (attestation class) | one |
| ledger writer | single appender, signs checkpoints, hands attestation requests to the gateway through the outbox; no network egress of its own; on start it checks its segment against the highest externally attested checkpoint and refuses writes on a mismatch (ADR-026) | exactly one per epoch (invariant L-12) |
| verifier service | public verification of bundles (the same code as the offline verifier) | stateless |
| workers | on-demand investigations, challenge batches, ranking jobs | a few, queue-driven |
| monitor | heartbeat watcher, synthetic probes | on a second host |

One VM per environment with a replica and a monitor elsewhere (`OPERATIONS_ARCHITECTURE.md`); local mode is the same code with recorded fixtures.

## 3. Where each hard requirement is satisfied by structure

| Requirement | Structure | Proof so far |
|---|---|---|
| An outcome can never be scored against something it did not forecast | contracts D, B, P, A with named events; settlement is a pure function of the forecast and the oracle series | specification; E5 (contract D arithmetic); the second settlement implementation is built in Node and compared with the Python one (`settlement-js`, `tests/test_settlement_differential.py`) |
| No future information | frozen snapshots; `known_at`; the origin after the attestation close; leak check on interventions | E16 model; E7/E8 on synthetic data; real tests not built |
| The operator cannot rewrite history unnoticed | two independent time-stamp operators over checkpoints; offline verifier | E2a, E2b, E2c (live tokens, <!--n:e2c-->48<!--/n--> cases, two languages) |
| A source failure never becomes a number | typed unavailable states; `null` never zero; `FAILED_EVIDENCE` is not an abstention | the fault-injection matrix; the venue connectors are built and tested on recorded and loopback exchanges (`venue_*`, `collect`), the RYO connector is built and switched off |
| One next check for agent and interface | `Research.select_next_check` | built for the claim reader (`claim_next_check`, dependency order, a person-run registry); the desk's gateway menu is not built |
| Untrusted content cannot act | typed model output, gateway menu, deterministic gates, kernel only shrinks | the AgentDojo-style suite is built (`injection`, a grid against the real defences with a scripted worst-case model, labelled simulated); not run with a real model |
| Private data stays private | private by default, digests only in the ledger, sentinel tests | specified |
| Honest labels | closed lists, templates, lints | E11: the lint caught one of the author's own mistakes (a state chip where an uncertainty chip belonged) |

## 4. Flows

**Cycle (league):** schedule → heartbeat token → discovery collection → manifest sealed (hash time-stamped) → evidence collection → snapshot frozen and hashed → arms decide before the cutoff → one batch commitment with independent tokens → attestation closed → origin `T0_cycle` → forecasts open → settlement after each horizon. Failures: late arm `FAILED_LATE`; stale evidence → `CYCLE_ABANDONED` for the horizon; outage → `FAILED_EVIDENCE`, never a call.

**On-demand investigation:** claim text → claim graph preliminary page (templates only) → gateway checks in priority class P2 → statuses arrive → completed or partial audit → optional publish with a preview → optional Verify commitment. The league is untouched.

**Evidence challenge:** receipt → intervention plan with leak check → paired replays with controls on tape → paired statistics → label and mechanism → new linked record.

**Verification:** download bundle → `verify` offline → four-part verdict.

**Learning:** settlement → outcome review (descriptive labels) → typed lesson candidates → shadow versions beside the parent and a null lesson → promotion once at the endpoint → holdback and possible rollback by a new locked version.

## 5. The flagship journey across components (one claim, one autonomous investigation, one evidence challenge, one independently timed record, one honestly settled outcome)

A visitor opens Today (precomputed, ranking engine) → pastes a claim (Claims) → sees the preliminary page, then checks arriving (Gateway, Evidence, Claims, Research with `select_next_check`) → reads what we tried to disprove (falsification protocol) → challenges one evidence item (challenge engine, E7/E8 statistics, mechanism attribution) → the committed thesis is time-stamped (ledger, verification; offline bundle) → the outcome settles under the contract (settlement) → the review proposes a lesson, which is only a hypothesis (learning spec). Each step has a specification in this package; the only step with a working artefact is the verification bundle (E2c) and the Track 3 skill.

## 6. What the architecture does not settle

Model quality and cost (no provider chosen, no key); RYO's real limits, shapes and status semantics (no key); whether people understand and use the interface (studies not run); whether any agent beats a baseline (leagues not run); power-loss durability and target-host behaviour; independent review of anything (no reviewer other than the author and outside document reviewers has seen the code or run the spikes). Every one of these is a named experiment or a named blocker, not an assumption.

## 7. Reading order

`CONTRACT_FINDINGS.md` (what the work changed in the product contract), `DECISIONS.md`, `INVARIANTS.md`, then per area: shared truth (`DOMAIN_MODEL`, `LEDGER_SPEC`, `ASSET_REGISTRY_SPEC`, `DATA_PROVENANCE`), external contracts (`CONNECTOR_CONTRACTS`, `GATEWAY_DESIGN`, `DISCOVERY_MANIFEST_SPEC`), decision mathematics (`SIMULATOR_SPEC`, `EVALUATION_PROTOCOL`), intelligence (`AGENT_PROTOCOL`, `LEAGUE_EXPERIMENT`, `CLAIM_GRAPH_SPEC`, `GROUNDING_CONTRACT`, `FALSIFICATION_PROTOCOL`, `EVIDENCE_CHALLENGE_SPEC`, `SUFFICIENCY_EXPERIMENT`, `OUTCOME_LEARNING_SPEC`), trust (`TRANSPARENCY_LOG_SPEC`, `VERIFICATION_SPEC`, `VERIFIER_THREAT_MODEL`), human (`UX_SPEC`, `ACCESSIBILITY_SPEC`, `HUMAN_AI_INTERACTION_SPEC`, `RANKING_SPEC`, `SOCIALFI_CONTRACT`, `ARENA_PROTOCOL`), operations and security (`SECURITY_MODEL`, `PRIVACY_DATA_FLOWS`, `OPERATIONS_ARCHITECTURE`, `OPERATIONS_SLOS`), specialist (`SOCIAL_SOURCE_GATEWAY`, `WHALE_EVIDENCE_CONTRACT`, `SKILL_CONTRACTS`), the whole-product proof (`INTEGRATION_PROOF`, `PROOF_REGISTER`) and the registers (`LICENSE_REGISTER`, `COMPETITOR_BASELINES`, `SOURCE_STUDY_LEDGER`, `TRACEABILITY`, `BUILD_STATE`).
