# Cycle runner: one league cycle, unattended

Scope: discovery, scheduler. Status: BUILT and TESTED (as-built notes in section 7, how to run it in section 8); a first exploratory run against the real world is 5.11h. Product rules: contract sections 6, 12, 16, 17, 25 and 27; `DISCOVERY_MANIFEST_SPEC.md` sections 1, 6 and 7; `RULES_ARM_SPEC.md`. Written before the code. What it runs today is the controlled league with one arm, the rules arm `L0`, on live exchange data; the RYO route is off until the metered probe is approved (the manifest records it as failed with that reason, which is the truth), so no cycle depends on RYO.

## 1. The protocol document

Everything a cycle may vary is fixed in one canonical document whose SHA-256 is the `protocol_hash` of every record the cycle writes. Changing anything makes a new protocol and so new cycle ids (`cy` over the protocol hash and the slot). Members:

| Member | Meaning |
|---|---|
| `name`, `format` | a label and the document format version |
| `schedule` | `start_ms`, `every_ms`: the slots, `start + k * every` |
| `horizons_hours` | `[1, 4, 24]` (the rules arm's) |
| `manifest` | `scan_n`, `event_cap`, the scan rule version and hash, the event rule version and hash, the watchlist symbols and their configuration hash |
| `oracle` | the pair id (`A`: USDT quote), the primary venue and the second venue, the quote class, the instrument kind |
| `collect` | the windows read for each candidate (`[60 s x 120, 3600 s x 40]`), the wait per call, the freshness limits by family kind, the required sources (`hoshitori.features`) |
| `arms` | `L0` with the SHA-256 of its frozen mapping |
| `timing` | the trusted-root version and hash the cycle was run under, the clock-offset limit, the snapshot freshness limit (restart rule) |
| `gaps` | nothing: the gap bound and the cutoff are functions of the horizon in `timeline` |

The document is read strictly (members, types, ranges); an unknown member is an error.

## 2. A cycle, step by step, and what each step writes

Every step is a `cycle_state` record (`cycle.py`), every item and decision is its own record, and every record is committed in the one batch of step 8, except the state records and the evidence which are appended as the cycle goes (they are ledger records too; only the forecasts and the snapshot need the batch's independent time stamp).

1. **PLANNED** at the slot: `cycle.begin`. The scheduler never plans a slot twice (the cycle id is a function of the slot).
2. **DISCOVERING**: record `discovery_at`. Read the event triggers from the asset registry's diff (`triggers.registry_triggers`, window since the previous cycle's discovery), the watchlist, and the RYO scan (failed: "the RYO route is off"). Store them as evidence items and keep their hashes.
3. **SEALED**: `manifest.build` with a resolver over the asset registry (an asset has an oracle if the registry has a resolved instrument of the oracle kind and quote class on the primary venue); record `sealed_at`, the manifest record and its hash. A cycle whose manifest has no candidate is legitimate: it commits the manifest and the snapshot with no forecasts.
4. **COLLECTING**: `collect.collect` for the union, through the gateway; the evidence records are appended; failures are kept.
5. **SNAPSHOT_FROZEN**: `snapshot.freeze` with `required = ["hoshitori.features"]`; `snapshot_at` is read from the clock at freezing; the order constraint of `timeline.cycle_order_ok` is enforced by `cycle.advance`. A freezing that fails, or a clock offset beyond the limit, or `snapshot_at - discovery_at` beyond 10 minutes, abandons the cycle with a reason from the closed list.
6. **RUNNING**: for each candidate and horizon the arm reads the snapshot through an independent reader (`rules_arm.decide`) before the decision cutoff. A `CALL` becomes a directional forecast (`forecast.DirectionalForecast`) with `p`, the invalidation, the evidence ids with digests, the observation-set hash and `interpretation_visible = false`; a `NO_CALL` or `FAILED_EVIDENCE` becomes the corresponding slot end. Each slot has a lifecycle history (`lifecycle`) from SCHEDULED to DECIDED, NO_CALL or a failure.
7. **COMMITTING**: `batch_root` is the root of the checkpoint to be.
8. **Batch commit**: the manifest, snapshot, forecasts and lifecycle records go to `commit.commit_batch`; the attestation asks the authorities through the gateway; the level of each forecast is the one its record reached.
9. **COMMITTED**: record `commitment_id`, `committed_at`. Each forecast slot then moves to COMMITTED(level) and AWAITING_ORIGIN with the origin `commit.origin_ms`; each horizon whose gap `origin - snapshot_at` exceeds its bound is `VOID_GAP` for that horizon (`timeline.void_gap`) and says so.
10. **Verification**: a bundle of the batch is exported and verified offline against the trusted root; the verdict is kept with the cycle. A cycle whose own bundle fails to verify is reported.

Any exception in steps 2 to 8 ends the cycle `ABANDONED(reason)`; nothing of an abandoned cycle's snapshot is committed later (contract section 25 item 9).

## 3. Settlement jobs

A scheduler tick also looks for forecasts whose horizon has matured (`origin + horizon + one candle`). For each: read the two oracles' candles for the reference candle and the outcome candle (and each venue's clock) through the gateway, run `settlement.settle_directional` with `retries_exhausted` false until `settlement` says the retries are over (up to two retries across the next hour, contract section 7), and append the settlement and the lifecycle step. A forecast that cannot be read is `PENDING` and tried again at the next tick; one that stays unreadable after the retry window is `UNSETTLEABLE_MISSING`. A settlement is a new record and never edits the forecast.

## 4. The scheduler

A loop that computes the next slot from the protocol, runs a cycle when its slot has come, runs the settlement job on every tick, and sleeps until the next event. Its state is the ledger: on start it rebuilds every cycle's history from the `cycle_state` records (`cycle.rebuild`); an unfinished cycle is recovered by the rule of `cycle.recover` (only a fresh frozen snapshot with no arm output resumes; every other is closed `ABANDONED(PROCESS_LOST)`); the heartbeat (`cycle.heartbeat`) lists slots that never started, never finished, finished late or were abandoned. Missed slots are not made up: a slot that passed while the process was down is a MISSING finding, never a late cycle.

## 5. What this does not do

No RYO tool is called. No arm but `L0` runs, so there is no comparison and no claim of skill: the output is a committed, independently time-stamped record of a fixed rule's probabilities and how they settle, which is what the model arms will later be measured against. Contract B and the practice portfolio are not run. The second oracle is read only at settlement. News, chain and social evidence are not collected. The live run uses the public exchange APIs and public time-stamp authorities: nothing secret leaves the machine, and the digests sent to the authorities are checkpoint hashes.

## 6. Tests the build must pass

Each step alone with a scripted gateway; a cycle end to end on fake exchanges and fake authorities, ending in a bundle that the offline verifier calls C3; a cycle that fails at each step (the clock, a collection that returns nothing, a freezing refused, an attestation that fails, an exception in the arm) and ends `ABANDONED` with the right reason and nothing committed; a restart at each state; a slot missed while down; settlement of a matured forecast to each outcome (up, down, tie, one oracle missing, the oracles disagreeing); the same cycle run twice gives the same ids and no duplicate record; a mutation sweep of every module.

## 7. As built (what differs from the text above, and why)

* **Modules.** `protocol` (section 1; slots, `is_slot`, `slots_through`), `oracle` (which two instruments settle a token), `registry_job` (the refresh of the asset registry from each venue's list, and the envelope in which registry records go into the ledger), `league` (section 2), `settlement_record`, `settle_job` (section 3), `recovery`, `commitment_store` and `scheduler` (section 4).
* **The manifest is appended at the seal, not in the batch** (CF-53). The committed and no-call steps of the slots, the wait for the origin and the final step of the cycle are appended after the batch. The committing step is the last record of the batch and carries the Merkle root of the batch's other records.
* **A cycle is never resumed** (CF-54): the snapshot is in memory until its batch is committed. Open cycles are closed as abandoned for a lost process, their undecided slots failed as lost, their committed slots left to settle.
* **Missed slots are not made up** (CF-55): a start grace less than the interval; the heartbeat reports the rest.
* **The RYO route is off**: the manifest records the scan as failed with the reason `the RYO scan route is off until the metered probe is approved`.
* **Not built here:** contract B and shorts for `L0`; announcement triggers; the research-decision impact (the cap keeps the earliest); RYO, news, chain and social collection; the second arm; the practice portfolio; the basis flag (`BASIS_STRESS` needs pair B).

## 8. Running it (unit 5.11g)

`hoshitori-league` (module `hoshitori.league_cli`) builds a running league from a settings file and runs the scheduler of section 4.

1. `hoshitori-league init-key FILE ORIGIN` makes the log signing key (owner-only permission, never overwritten) and prints its public key as a trusted root lists it. A key file that others can read is refused.
2. The trusted root names that public key and the time-stamp operators (a release root is made and signed by the owner of the root keys; for an exploratory run `scripts/make_exploratory_root.py` builds one from the real authorities with a throw-away root key, so it can never be updated and a run under it is not a scored period).
3. `hoshitori-league make-protocol OUT --root FILE --mapping ARM=FILE ... --name N --start-ms T --every-minutes M --watchlist A,B,...` writes the protocol file pinned to that root and to the frozen mappings (`--primary` and `--second` name the two oracle venues; both must be reachable from the host that runs the league, CF-60; `--barrier-mapping ARM=FILE` pins a barrier mapping for contract B).
4. The settings file is JSON with exactly these members, each checked: `directory`, `protocol`, `mappings` (arm to file), `barrier_mappings` (arm to file; empty unless the arm also forecasts contract B, and then the protocol pins its hash), `trusted_root`, `log_key`, `source` (a urn), `venues` (name to a base URL or null for the venue's own), `authorities` (name and URL), `lists` (venue, scope and, where a venue needs it, kind), `tick_every_s`, `start_grace_ms`, `finish_within_ms`, `wait_s`, `allow_insecure_loopback` (tests only) `allow_plain_http_authorities` (off by default; time-stamp authorities only, CF-59) and `practice` (true or false: the practice portfolio runs after the settlement pass, `SIMULATOR_SPEC`). Relative paths are read against the settings file's directory. Assembly refuses a protocol pre-registered under another root, an unknown venue, a missing primary oracle venue, too few authorities for the root, a log key the root does not allow for the ledger's size, a grace that is not less than the interval and a cycle that has no time to finish.
5. `hoshitori-league run SETTINGS [--once]` ticks until a terminate or interrupt signal (after the tick it is in) or once; one JSON line per tick; `status SETTINGS` counts cycles, slots, forecasts, failures and settlements and lists the slots the heartbeat reports; `export SETTINGS CYCLE OUTDIR` writes the bundle of a committed cycle for `hoshitori-verify` (and for the Node verifier). Exit status 0 on success, 1 for a failure of the run or the command, 2 for settings that cannot run.

