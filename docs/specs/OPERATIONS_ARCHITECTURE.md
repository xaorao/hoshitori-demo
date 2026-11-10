# Operations architecture

Scope: deployment, scheduling, backup and restore, observability, runbooks. Status: DESIGNED; the backup method was measured (E12, E12b, E12c); everything else is a plan. Product rules: contract sections 12, 16, 25, 33, 42; standard 5d; description 5. Sources studied: SQLite documentation (atomic commit, how to corrupt a database, the online backup API: read), Litestream (Apache-2.0; README read; status beta), OpenTelemetry specification (cloned; semantic conventions not read in full), Temporal and DBOS (see AGENT_PROTOCOL; E4).

## 1. Shape

One always-on virtual machine per environment (AWS decided earlier; **VM size and the target-disk durability are NOT VERIFIED** and are the first measurement on the host, ADR-001) running: the web and API process, the scheduler, the gateway, the ledger writer, the verifier service and a reverse proxy; a static CDN in front for Today and bundles; an object store for closed ledger segments, raw payloads and encrypted backups; one replica of the ledger segments on a second provider or account (witnessing is separate and does not replace replication). Local mode is the same code on a developer machine with fixtures.

## 2. Backup and restore (ADR-012; measured)

| Data | Method | Evidence |
|---|---|---|
| Closed ledger segments | copy to the object store and the replica after their SHA-256 is recorded in the next manifest line; verify on arrival | immutable by rule; ledger spec section 4 |
| Operational SQLite (outbox, index, private items) | the SQLite **online backup API** (or `VACUUM INTO`), never a file copy of a live WAL database | **E12c: 60 of 60 online backups of a live database under a continuous durable writer were consistent (integrity check ok, hash chain intact from the first row, row count between the counts before and after). E12b: 35 of 60 plain copies of the main file alone failed to open ("database disk image is malformed"), 5 more failed the integrity check and only 20 verified.** E12: a 410 MB database backed up in 10.9 s while the writer ran; the writer's commit latency rose from p99 0.05 ms to 0.47 ms and from a maximum of 2.9 ms to 25 ms (this machine, process-kill durability only) |
| Raw third-party payloads | object store with the hash in the index; lifecycle per data-rights rule | contract 16 |
| Private items | encrypted with a per-item key; backups hold ciphertext; deletion destroys the key | privacy spec section 3 |
| Keys | secret store plus an offline escrow of the log key under a split procedure (to design with the first key ceremony) | security model |

**Litestream is not adopted for the ledger.** It is a good fit for continuous replication of an application database, but its README marks it beta, it adds a lock table to the source, and our ledger is files plus a rebuildable index, so the cheaper and more verifiable path is segment replication plus periodic online backup of the index. It remains an option for the private-items database if the recovery-point objective requires it (a measured decision, not made).

**Restore drill (required, scheduled):** restore the latest backup and replica on a clean machine, rebuild the index from segments, compare derived tables byte for byte, run the verifier on a sample of bundles, and record the time taken; a backup never restored is not a backup. **Not verified:** the restore time at realistic size; behaviour on the target host; power-loss durability (macOS `fsync` does not flush the drive cache without `F_FULLFSYNC`; the results here prove process-kill durability only, as E3 stated).

## 3. Scheduler and workflows

The scheduler is the project's own thin state machine (ADR-006): the cycle state machine of the manifest spec with durable state in the ledger and outbox. DBOS was measured (E4): 30 SIGKILL rounds, 10,814 workflows, no duplicate ledger line, at 383 workflows per second against 1,830 for the plain outbox; a duplicated external side effect occurred once (an external done-mark) because durable execution is at-least-once for external effects; both designs need idempotent external calls. Nothing in the plan needs more than the outbox. Heartbeats: each cycle start and finish is time-stamped (contract 12) and a monitor on a separate host alerts if a cycle did not start, finish or finish late.

**Failover is a manual, fenced act (ADR-025, E21).** The primary is never replaced because a health check failed. An operator (or a documented automation that only prepares the steps) takes the next epoch with a compare-and-swap in the linearizable pointer store, the candidate shows it holds the last published checkpoint with a consistency proof, the log records the epoch change with the closing size of the old epoch, and the old primary's key is retired in the trusted root. A candidate that cannot prove continuity does not become a writer; the log stays stalled and visible, which is the safe state. A stale primary that keeps signing locally cannot publish (the compare-and-swap fails) and its checkpoints are rejected by every verifier (larger than the closing size or inconsistent with the published chain). Tests: simultaneous promotion, partition, stale replica, reused key and divergent checkpoints (E21, model); the same drills on the target host are to build.

**Pending commands and disaster recovery (ADR-027, E22).** Committed history is rebuilt from the replicated segments; pending work is not in them. A command is acknowledged only after its intent is in the fsynced intent journal (replicated like the segments); a restore of an older SQLite replays the journal intents missing from the ledger; producers keep receipt ids, and an acknowledged id found nowhere is written as FAILED_LOST with the reason. Remote time-stamp requests in flight are repeated by digest and counted once per operator. Drill: lose SQLite after an acknowledgement and before the first append, restore a backup, check that the command is appended exactly once or recorded as FAILED_LOST.

## 4. Deployment and change control

Immutable build artefacts with the lockfile hash; blue-green or restart-in-place with the ledger writer stopped between segments (a deploy never changes the history: the post-deploy check re-verifies the last checkpoint and the tree head); database migrations are forward-only scripts tested on a copy of production data; a feature flag set per environment, recorded in the cycle record so a cycle's behaviour is reproducible; model ids and prompt versions are part of the agent version hash and never change inside a scored period (contract 23). The gateway's rate-limit state is rebuilt from the observed headers after a restart, never assumed.

## 5. Observability (standard 5d)

OpenTelemetry traces with the cycle id, the candidate id, the agent version hash, the data-contract version and the RYO schema version on every span; metrics per source (success, status, latency, `data_mode` distribution, null rate), per arm (completion, failure class, latency, tokens, cost), per gateway class (queue depth, 429s, circuit state), ledger (append rate, WAL size, checkpoint lag, outbox age), and verification (token acquisition time per authority, level reached). Dashboards for the operator only; nothing private goes to a third-party service. Drift: data-contract drift checks on RYO fields (the probe's schema comparison) and the pass or fail style of Evidently reports (idea only).

## 6. Runbooks (each a tested procedure)

RYO unavailable or rate-limited; RYO schema changed; model provider outage; exchange or oracle outage; time-stamp authority unreachable; ledger writer crash; WAL growth; disk full; segment corruption; key exposure; cycle abandoned; backup failed; restore; a deploy that must be rolled back; abuse flood.

## 7. Capacity (priors to measure)

Expected load is a few cycles an hour with at most about 20 candidates, tens of on-demand investigations an hour, and a modest read load on a static page. The binding constraints are the RYO rate limit (6 per minute observed by third parties, model unknown, `--measure-limit` pending the key) and model spend, not compute; E3 showed about 5,300 durable commits per second on one core. A load test at ten times the expected rate for 24 hours is required on the target host before release.

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Process killed at any step | recovery first (scan and reconcile the segment tail, never re-append), then drain the outbox | E20: real kills at five points, 300 rounds, 0 violations (the first-written protocol failed 180 of 180 at three points); E3 and E4 cover the SQLite-only and workflow cases |
| Backup taken during heavy writes | consistent copy | E12c repeated on the target host |
| Restore of an old backup | the verifier reports the missing tail against the replica and the tokens; nothing silently shorter | restore drill |
| Segment or index rolled back below an attested checkpoint | the writer compares the segment with the highest attested checkpoint; it restores the prefix from a replica whose root matches or enters INTEGRITY_FAILURE and writes nothing | E20b: 0 of 600 rounds resumed over a broken prefix; the same rolled-back files made plain reconciling recovery resume in 500 of 600 |
| Replica promoted while the primary is alive | the epoch compare-and-swap decides; the loser fails closed; stale checkpoints are rejected by the acceptance rule | E21 model; drill on the target host to build |
| SQLite lost after an acknowledgement | intent journal replay; unrecoverable acknowledged ids become FAILED_LOST | E22: 0 silent omissions with journal and receipts |
| Clock jump on the host | commits refused above the offset limit (domain model) | fault injection |
| Region or VM loss | restore on a new VM from replicas; the public record survives in the replica, the time-stamp tokens and any witnesses | disaster drill |
