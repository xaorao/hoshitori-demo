# Invariants

Scope: checkable properties. Status: the lifecycle and timing subset is **modelled and property-tested** (E16: a reference model plus a Hypothesis stateful test, 2,500 examples of up to 70 steps, every lifecycle state reached, 8 deliberately introduced faults all caught); the ledger subset is covered by E1 to E4; the rest are stated here with their checking method and are **not yet tested**. Source: contract section 23 (every invariant of that section appears below, regrouped), ledger spec section 7, the findings of this package. An invariant is a sentence a machine can falsify; a rule that cannot be checked is listed under "not checkable".

## 1. How each is checked

`PROP` property or stateful test; `UNIT` golden or unit test; `LINT` static check on code, view models or text; `CI` repository check; `RT` red-team case; `MODEL` the executable model of E16; `OBS` runtime monitor that raises an alert.

## 2. Ledger and canonical records

| ID | Invariant | Check | Status |
|---|---|---|---|
| L-1 | `seq` gapless and increasing; tree size equals the number of appended records | PROP | **tested** (product): `test_ledger_stateful`, `ledger_checks.invariants` |
| L-2 | closed segment hash equals the value in the next manifest line | UNIT, OBS | **tested** (product): `test_segment` manifest chain, `rotation_and_reopen` |
| L-3 | every checkpoint root equals the tree head recomputed from the segments | PROP | **tested** (product): checkpoints checked against the recomputed tree on every open (`prefix_protection`) |
| L-4 | sizes never decrease; two checkpoints are consistent; the writer refuses a different root for one size | PROP | **tested** (product): `checkpoint_guards` (smaller size and different root refused), sizes of signed checkpoints checked on open; consistency of two checkpoints follows from the single tree and is exercised by `basic_api` |
| L-5 | `leaf = SHA-256(0x00 ‖ canonical(rec))` and `canonical(rec) = rec` | PROP | E1: 146 vectors, 10,000 mutated inputs, Python and JavaScript agree |
| L-6 | a conflicting record for an existing `(source, id)` is never appended | PROP | **tested** (product): `basic_api` conflict cases, `test_ledger_stateful` duplicates |
| L-7 | rebuilding the index from segments yields byte-identical derived tables | CI | **tested** (product): `index_and_journal_recovery` (lost index rebuilt to identical rows) |
| L-8 | every hash on the wire is exactly 32 bytes; tree size is taken only from a verified checkpoint | UNIT | E2a, E2c |
| L-9 | after any crash the segment has no duplicate line, a gapless sequence and valid leaf hashes; the segment is the source of truth and recovery never re-appends a line that is already present | PROP (kill injection) | **tested** (product): `crash_sweep`, 150 random process kills, named-point kills |
| L-10 | no index row points beyond the end of the segment; the index equals a rebuild from the segment after recovery | PROP | **tested** (product): `index_and_journal_recovery`, `torn_and_damaged_tails` |
| L-11 | an id names exactly one thing: a fetch (capture) and a content (hash) never share an identity; identical bytes fetched twice are two observations sharing one blob | PROP | **tested (E19)** |
| L-12 | at most one writer is active per epoch; a promotion requires a successful epoch compare-and-swap and a continuity proof from the last published checkpoint; without both the candidate fails closed | MODEL, PROP | **tested** (product, single process): `failover_checks` (simultaneous promotion, split-step race); a real store is open |
| L-13 | the segment's prefix up to the highest externally attested checkpoint is intact before any append; otherwise the log is in INTEGRITY_FAILURE and refuses writes | PROP, FI | **tested** (product, file level and process kills): `prefix_protection`, `replica_restore`; power loss not tested |
| L-14 | an acknowledged command ends in the ledger or in a FAILED_LOST record with its reason; none is silently absent | PROP, FI | **tested** (product): `basic_api`, `lost_commands`, journal replay, named-point kills |

## 3. Lifecycle and timing (MODEL)

| ID | Invariant | Check | Status |
|---|---|---|---|
| T-1 | terminal states are absorbing | MODEL | **tested (E16 model); product:** `test_random_walks_keep_the_laws`, `test_guided_walks_keep_the_laws` |
| T-2 | a committed forecast is never modified (content fixed at commit) | MODEL | **tested** |
| T-3 | a forecast cannot settle before it is committed, nor before `T0 + horizon` | MODEL | **tested (model); product:** `check_member_rules` (a slot settles no earlier than its horizon after its origin) |
| T-4 | `FAILED` is impossible after `COMMITTED`; after commitment only `SETTLED`, `VOID`, `UNSETTLEABLE` | MODEL | **tested (model); product:** `test_random_walks_keep_the_laws` (a committed forecast was never abandoned) |
| T-5 | `T0` strictly after `eligible_after`; at C3 or above `eligible_after = attested_at + u` | MODEL | **model only**: the product lifecycle accepts a C3 origin before `attested_at + u` and its checks pass (reproduced) |
| T-6 | `attested_at` never moves earlier; a counted token lies in `[committed_at − 1 s, committed_at + 60 s]`; C3 or above needs two counted tokens | MODEL | **tested (CF-22 model); product:** `check_late_levels` |
| T-7 | the origin is set only after the attestation window is closed (two counted tokens or `committed_at + 90 s`) | MODEL | **tested (CF-23 model); product:** `check_late_levels`, `test_random_walks_keep_the_laws` (C3 first reached after the close) |
| T-8 | an illegal action leaves the state unchanged | MODEL | **tested (model); product:** `test_random_walks_keep_the_laws` (`walk`: accepted means one step added) |
| T-9 | `T0 > authorized_at` and `fill_time ≥ max(authorized_at + L, T0)`; the fill candle is never the candle that closes at `T0` | PROP | **tested** (product): `check_clocks` (every one of the 60 second-offsets) |
| T-10 | every forecast of one league cycle shares `T0_cycle`; no arm output is accepted after the decision cutoff | PROP | **tested (shared origin):** `league_checks.check_happy_path` (every slot of a cycle takes the one origin); the cutoff half is not tested here: to build |
| T-11 | the snapshot-to-origin gap of every scored forecast is within its horizon bound; arena `W` plus margins within its bound | PROP | **tested (league cycles):** `league_checks.check_levels_and_gap` (a horizon whose gap bound is passed is void for that horizon and the others of the cycle are scored); the arena window is not built |
| T-13 | the committed time and any declared window are read from the signed record inside the tree, never from an unsigned bundle file | PROP, UNIT | **tested (E2c: editing the signed fields fails integrity; the first verifier failed this, `demo_old_hole.py`)** |
| T-14 | a trusted-root update is accepted only with a threshold of the old and of the new keys, exactly one version higher, within the chain bound, from channels that agree; the final root is unexpired against a fixed start time | UNIT, PROP | **tested in two languages (E24, 23 cases, six mutants)** |
| T-15 | a checkpoint is accepted only if consistent with every published checkpoint and not larger than the closing size of the epoch whose key signed it | MODEL | **tested in a model (E21)** |
| T-12 | no calibration version is applied to a period it was trained on; an agent version never changes inside a scored period | PROP | not built: no scored period exists, so no calibration or agent version is applied to one |

## 3a. Verification and egress

| ID | Invariant | Check | Status |
|---|---|---|---|
| V-1 | a verdict about our log requires a trusted root from outside the bundle; without one the verdict is `BUNDLE_ASSERTED` and never scorable; the bundle's own policy is never believed | UNIT | **tested (E2c: whole-bundle substitution and offline forgery with self-made authorities)** |
| V-2 | two operators count as independent only if their registry parents differ and both entries carry ownership evidence | UNIT | **tested (E2c)**; registry content not established |
| V-3 | a public-commit claim never raises a level offline | UNIT | **tested (E2c)** |
| V-4 | hostile or malformed input never crashes a verifier and never yields a pass | PROP, fuzz | partly tested (a malformed time crashed one verifier and was fixed); full fuzzing to build |
| V-5 | ownership evidence counts only if it is a structured record of a kind the trusted root accepts, with a reference, about the very root the token chained to | UNIT | **tested (E2c, 48 cases, both languages)** |
| N-1 | every outbound connection is made by the gateway; no other process has egress | RT | **tested (static):** `test_network_boundary.test_network_clients_are_imported_only_by_the_gateway_transports` (no module but the three transports imports a network client); a run-time connection attempt from the writer or the web process is not tested |
| N-2 | the seven integration properties (same truth, authority, history, failure, privacy, outcome, reasoning) hold across every connection of the pipeline, and each has a mutant that breaks it | INT | fixture level only (E25); genuine journeys to build |

## 4. Evidence, claims and records

| ID | Invariant | Check | Status |
|---|---|---|---|
| E-1 | no intervention leaves a descendant of a removed source in the context | PROP | **tested:** `intervention_checks.check_leak`, `model_challenge_checks.check_leak` |
| E-2 | the independence count never counts two items of one origin family twice | UNIT | **tested:** `lineage_checks.check_independence` (items of one family count once) |
| E-3 | a claim node's `source_span` is immutable; a person's edit is a separate `user_reading` | PROP | partly: a node's spans are checked against the text when the graph is built (`claim_graph_checks.check_refusals`); the person's separate reading record is not built, so the immutability is not tested as such |
| E-4 | support never propagates along a claim-graph edge; a composite claim never receives a single verdict | PROP | **tested:** `claim_graph_checks.check_propagation` (support does not travel along an edge) |
| E-5 | an assertion is shown as supported only if its layered support check passed and the judge was validated for that stratum | UNIT | not built: the support check and its validated judge |
| E-6 | `asset_id`, quote class and instrument kind are identical at every link of the identity chain | PROP | **tested:** `registry_checks.check_resolution`, `registry_checks.check_multipliers_and_variants` (a quote class, a multiplier or an instrument kind that differs is refused as an identity mismatch) |
| E-7 | an on-demand fetch never alters a frozen snapshot or any league record | PROP | partly: `gateway_checks.refusals` (a league class outside the league and an on-demand class in the league are refused) and `snapshot_checks.check_frozen` (a frozen snapshot is not changed); no check runs an on-demand fetch against a live league and looks for a changed record |
| E-8 | an evidence-acquisition policy chooses only among registered menu checks; a layer-1 next check is a registered check that can be run or performed now, or `NO_AVAILABLE_CHECK` | PROP | **tested:** `claim_next_check_checks.check_off_menu`, `claim_next_check_checks.check_menu_mode` (a check that is not on the menu is refused with its id) |
| E-9 | no social item below provenance level SP-2 enters public creator reputation | UNIT | not built: the social gateway and creator reputation |
| E-10 | a chart's text and table are generated from the same data object as the chart | UNIT | **tested (browser):** `practice_test` (the curve's drawn points, its table and its sentence come from the same marks); it is the one chart there is |

## 5. Scoring, comparison and learning

| ID | Invariant | Check | Status |
|---|---|---|---|
| S-1 | a displayed score state equals the output of the single eligibility service | UNIT | not built: the service that gives a score its display state (no board ranks yet) |
| S-2 | head-to-head tables never mix information classes or cycle classes | PROP | not built: the league tables that would mix classes |
| S-3 | the arms never receive RYO interpretation, even in an RYO-read audit | RT | **tested:** `arm_input_checks.check_refusals` (a reader that can read interpretations is refused for an independent arm), `arm_input_checks.check_what_a_model_sees` |
| S-4 | a candidate outside the sealed manifest is never scored; the manifest is never edited after sealing | PROP | partly: `snapshot_checks.check_refusals` (evidence collected for a candidate outside the manifest is refused); an edit of a sealed manifest is not tested as such |
| S-5 | the visible slate never has more than two of its top five overlapping in information unless each exception states why | PROP (E13 planted cases) | not built: the slate of Today |
| S-6 | a falsification outcome can only reduce exposure | PROP | **tested:** `desk_gates_checks.check_properties` (over thousands of random inputs from a fixed seed no path gives an exposure above the ceiling, above the pre-test target after a failed step, or a short) |
| S-7 | a minimal-sufficient-evidence result is never shown without held-out confirmation, perturbation distribution and model version | UNIT | not built: minimal sufficient evidence |
| S-8 | an outcome review never changes a live agent; a lesson reaches an agent only as a new locked version validated forward and promoted once, at its endpoint; a rollback creates a new locked version | PROP | not built: the outcome review and the learning ledger |
| S-9 | a person's reading never enters a league table, a forecast, a receipt or a score | RT | not built: the person's reading |
| S-10 | a decision change caused by a deterministic gate is never labelled as the model changing its judgment | UNIT | **tested:** `model_challenge_checks.check_mechanism` (a gate that stops it is not the model changing its mind) |

## 6. Product and security

| ID | Invariant | Check | Status |
|---|---|---|---|
| P-1 | nothing from a PRIVATE item is reachable from any public surface | RT (sentinels) | not built: private investigations and publishing |
| P-2 | no acceptance decision uses the number of agreeing identities | UNIT | not built: the adjudication of contributions |
| P-3 | a preliminary page has no field that can carry a check result and contains no verification word or stance | LINT | **tested:** `claim_preliminary_checks.check_lint`, `claim_preliminary_checks.check_page` |
| P-4 | a `BREAKING_UNVERIFIED` item never occupies the featured position and carries no stance, probability or verdict string | LINT | not built: the unchecked row of Today |
| P-5 | the Global Today order has no personal input; a first visit equals it | PROP | partly: Today has no personal input because personal updates are not built; no test pins it |
| P-6 | no instruction embedded in an untrusted channel changes a decision, exposure, ledger record or tool call | RT (AgentDojo-style grid) | **tested (a scripted worst-case model, labelled simulated):** `injection_checks.check_the_grid`, `injection_checks.check_the_real_defences` |
| P-7 | no secret appears in any prompt, log or committed file | CI, RT | **tested:** `test_secret_scan` |
| P-8 | every test count, version and revision quoted in a document equals the registry output | CI | tested by the research folder's registry tool (`--check`), which is not in this repository |
| P-9 | a participant who obtains information after the snapshot is `AGENT_LIVE_ACQUISITION` and is never placed in a head-to-head table with snapshot-only arms | PROP | not built: the live-acquisition class of the operational agent |

## 7. Not checkable by a machine (and what stands in)

"The decision was good", "the reasoning was sound", "the interface is understood": these are measured by the benchmark programs and studies, not asserted as invariants.

## 8. Finding about the method

Writing the model found two holes in the contract (CF-22, CF-23) before any code existed, and a first version of the property test passed while missing deep states: exploration reached only early states until forward-progress rules got preconditions (SETTLED was reached 0 times, then 14,816). A property test that does not report which states it reached proves little; the reached-state counts are part of the result (`../experiments/E16/reached_states.json`).
