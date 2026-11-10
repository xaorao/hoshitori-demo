# Discovery and candidate-manifest specification

Scope: event discovery and candidate-manifest fairness. Status: DESIGNED. The rules are the contract's (section 6 manifest and sequence, section 22 discovery recall, section 25 cycle protocol, section 46 routes); this file turns them into a state machine, record shapes, trigger rules and tests. Sources studied: the contract, Nota's daily lock (a fixed universe of 25 majors, read), Wagyu (a user-chosen token, read), World Monitor's behaviour (AGPL source, ideas only), Binance's announcement feed and the Coinbase and Kraken status pages (probed today, below), ForecastBench's question-set method (documentation read).

## 1. The cycle as a state machine

```
PLANNED -> DISCOVERING -> SEALED -> COLLECTING -> SNAPSHOT_FROZEN -> RUNNING -> COMMITTING -> COMMITTED
              |             |           |               |               |           |
              +-------------+-----------+---------------+---------------+-----------+--> ABANDONED(reason)
```

| State | Entry condition | Records written | Exits |
|---|---|---|---|
| `PLANNED` | the protocol hash exists and the schedule fires | `Cycle(planned)` | clock reaches `discovery_at` |
| `DISCOVERING` | discovery collectors run | `RawCapture`s of the RYO scan and the event-trigger log | all collectors finished or `discovery_deadline` |
| `SEALED` | manifest built by deterministic code from the stored inputs | `Manifest` (hash), `Cycle(sealed)`, time-stamped through the next checkpoint | `collect` window opens |
| `COLLECTING` | gateway window declared (gateway design, section 2) | `RawCapture`s, `EvidenceItem`s for the manifest's candidates only | collection complete or deadline |
| `SNAPSHOT_FROZEN` | snapshot hash written | `Snapshot` (hash of the item ids and hashes) | arms start |
| `RUNNING` | arms read the snapshot | arm outputs, as they arrive, up to the cutoff | cutoff reached |
| `COMMITTING` | accepted outputs batched | one checkpoint, tokens requested | tokens stored, or the level is recorded as reached |
| `COMMITTED` | commitment record written | `Commitment` | terminal |
| `ABANDONED(reason)` | any failure that the contract defines as abandonment (snapshot older than its freshness at commitment, `snapshot_at - discovery_at > D`, process lost between snapshot and commitment, clock offset above the limit) | `Cycle(abandoned, reason)` | terminal; nothing of its snapshot is ever committed later |

Constraints (contract section 6): `discovery_at <= manifest_sealed_at <= snapshot_at`; `snapshot_at - discovery_at <= D` (prior 10 minutes); nothing is added to a sealed manifest; an event arriving after the seal is a `post_seal_event` for the next cycle or the operational regime; `discovery_age_at_origin` is stored on every forecast. Every transition is a ledger record, so the transition log is itself auditable; an illegal transition is rejected and logged (lifecycle rule, contract section 17).

## 2. The manifest

One record per cycle, canonical JSON (record profile), hashed into the ledger:

```
{ "type": "manifest", "cycle_id": ..., "protocol_hash": ..., "discovery_at": ..., "sealed_at": ...,
  "inputs": [ {"kind": "ryo_scan_response" | "event_trigger_log" | "watchlist_config",
               "capture_id": ..., "sha256": ...} ],
  "rules": { "ryo_scan": {"rule_version": ..., "rule_hash": ..., "n": 10},
             "event":     {"rule_version": ..., "rule_hash": ..., "cap": 5},
             "watchlist": {"config_hash": ...} },
  "entries": [ { "candidate_id", "asset_id", "routes": ["RYO_SCAN" | "EVENT" | "WATCHLIST"],
                 "trigger_id", "trigger_provenance", "first_seen_at", "dedupe_key",
                 "route_rank", "inclusion": "INCLUDED" | {"EXCLUDED": reason} } ] }
```

- **Closed list of exclusion reasons:** `below_rank_cutoff`, `duplicate`, `capped`, `ineligible_asset` (registry status other than `RESOLVED` or no oracle), `no_oracle`, `trigger_not_qualified`. No free-text reason exists.
- **The eligible union** is the set of `INCLUDED` entries. Every arm receives exactly the union; nothing outside it is scored; an asset reached by two routes is one candidate carrying both tags.
- **Replay:** the manifest is a pure function of its `inputs` and `rules`. A replay test rebuilds it from the stored captures and compares hashes.

## 3. Routes and their rules

### `RYO_SCAN`
Top N of the stored `scan_market` response by RYO's own rank (N default 10, a protocol parameter). The raw response is stored and hashed first. RYO's `theme` is context, not a filter (guide), so the rule does not filter by theme.

### `EVENT`
A trigger is a rule that fires on a record of the event-trigger log. A rule is code with a version and a hash; the rule set is part of the protocol. Qualification (contract section 46.3): the trigger rests on a **primary document** (an exchange's own announcement, a status incident, a project's own post) **or** on at least two independent origin families (data provenance, section 3), and it names an asset the registry resolves. Duplicates by origin family collapse. At most E triggers (default 5) enter a cycle, ordered by research-decision impact and then `first_seen_at`; the rest are `EXCLUDED(capped)` and listed.

**Primary sources verified reachable today (no key):**

| Source | Finding |
|---|---|
| Binance announcement list (the website's own JSON feed for a catalogue) | returned structured articles with `code`, `title`, `releaseDate` in milliseconds; titles include listing and contract-launch notices (for example a futures contract launch notice). It is **not part of the documented API** (the API documentation repository has no announcements endpoint), so it is a `CONTRACT_DRIFT`-guarded source with its own terms question (NOT VERIFIED) |
| Coinbase status | the Atlassian Statuspage `summary.json` answered with components and an `updated_at` |
| Kraken status | `status.json` answered (HTTP 200) |
| Binance, Coinbase, Kraken instrument lists | the registry diff (new, delisted, status change) is itself a trigger source (asset registry, section 4) |

Rules (examples, to be fixed in the protocol; each is code with a version): `LISTING_ANNOUNCED` (primary document announces a spot or perpetual listing of an asset), `DELISTING_ANNOUNCED`, `VENUE_INCIDENT` (status page marks a component degraded for an asset's venue beyond a threshold), `REGISTRY_NEW_INSTRUMENT`, `MOVE_BEYOND_K_ATR` (price move beyond a pre-registered multiple of ATR in a window; independent of any announcement), and `NARRATIVE_CONVERGENCE` (independent voices converge, social provenance level SP-1 or SP-2 only). A rule that fires only on events that already moved price would bias discovery toward the past; `LISTING_ANNOUNCED` and `VENUE_INCIDENT` fire before the move.

### `WATCHLIST`
Only items on a watchlist whose configuration hash is fixed in the protocol.

## 4. Per-route accounting

Every result table exists per route and pooled; the pooled table is labelled pooled and states each route's share (contract section 6). The denominators: candidates in the manifest (all entries including excluded), eligible union, decided, committed, settled, per route and horizon. The removal test (does RYO add anything?) is reported per route, including how often RYO covered an `EVENT` token.

## 5. Discovery recall (measured before ranking)

An independent event list is built from public records before the evaluation window and frozen: exchange listing and delisting notices, protocol incident reports, venue incidents, and moves beyond a pre-registered multiple of ATR. For every listed event the report states whether it was in the candidate set, in the top k, and the time from the event's primary-source time to first detection; misses are listed. An event class no configured source covers is "not covered", not a miss. Events the system never discovered count in recall and are never excluded silently.

## 6. Heartbeat and missed cycles

Each cycle's start and finish are ledger records in a checkpoint that carries independent tokens; a public monitor lists cycles that did not start, did not finish or finished late, computed from the schedule in the protocol and the ledger. What is claimed: the record is consistent with the published collection protocol. What is not claimed: that it covers the whole market (contract section 12).

## 7. Scheduler (A32)

The scheduler is a loop in the single scheduler process; its state is the ledger and the `cycle` table. Each tick it computes the next cycle from the protocol; it never holds state only in memory. A restart reads the `cycle` table: a cycle past `PLANNED` that has no `COMMITTED` or `ABANDONED` record is closed as `ABANDONED(process_lost)` unless its snapshot is still within the freshness limit **and** no arm output exists, in which case it resumes at `SNAPSHOT_FROZEN` (the only resumable state). The schedule is a data file in the protocol, not code.

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| RYO scan fails | `EVENT` and `WATCHLIST` routes continue; the manifest records the RYO route as `FAILED_EVIDENCE`; RYO-covered arms are marked | scan outage fixture |
| Event feed shape changes | `CONTRACT_DRIFT`; the feed contributes no triggers; the manifest lists the failure | schema-drift fixture |
| Trigger after the seal | next cycle or operational regime; not added | post-seal fixture |
| Two routes, one asset | one candidate, two tags, counted once pooled and once per route | route fixture |
| E + 3 triggers | E admitted, 3 `EXCLUDED(capped)` | cap fixture |
| Manifest edited after the snapshot | rejected | tamper fixture |
| Snapshot collected later than D | `ABANDONED` | clock fixture |
| Process killed in each state | state after restart matches section 7 | kill rounds |
| Replay of a sealed manifest | same hash | replay test |

Connection to the journey: the first investigation a visitor sees is either a claim they bring (outside the league) or a developing item the manifest's `EVENT` route surfaced, so the demonstration does not depend on a momentum scan having already moved.
