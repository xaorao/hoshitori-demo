# Runbook

What to do when the league on its host needs a person. The layout is the one in `docs/DEPLOYMENT.md` (league folder `/var/lib/hoshitori/league`, backups `/var/lib/hoshitori/backups`, log key `/etc/hoshitori/log.key`, code in `/opt/hoshitori`). `PY` stands for `/opt/hoshitori/venv/bin/python`, `LEAGUE` for `/opt/hoshitori/venv/bin/hoshitori-league` and `SETTINGS` for `/var/lib/hoshitori/league/settings.json`. Run every command as the service user (`sudo -u hoshitori ...`).

Each procedure says whether it was run. "Run on loopback" means the command was run on the build machine against a league whose outside world was the loopback fake of `tests/e2e_league.py` (two exchanges and three time-stamp authorities through real sockets), with `hoshitori-league run` as the writer in its own process. The outputs quoted are from those runs, trimmed; they are test data, not a real league. No procedure has been run on a server.

## 1. Is it working?

```
PY -m hoshitori.host_probe SETTINGS --backups /var/lib/hoshitori/backups
```

One JSON line. `"ok": true` and exit 0 when every finding is good; exit 1 names the finding that is not. The health timer runs this every minute; `systemctl --failed` lists the health unit when a finding is bad, and `journalctl -u hoshitori-health` shows the line.

`LEAGUE status SETTINGS`, `site`, `export` and `replay` only look. They open the ledger and the capture store read-only, take no lock, ask for no log key and no model key, and write nothing, so they run while the league runs. The check that shows it keeps a real writer open, moves the log key away, and requires each of the four to succeed and every file of the league's state to be exactly as it was afterwards (`check_readers_do_not_write`). What they show is the ledger up to its last whole line: a line the league is part-way through writing is not yet a record. The health probe is what the timer runs, because it also reads the backup age and the free disk; this is the probe at a moment before any backup existed (trimmed):

`LEAGUE forge-demo SETTINGS CYCLE OUTDIR` also looks only, and needs no key: it makes a forged copy of a committed cycle's bundle for the Verify page (one call's chance changed, the log rebuilt and signed, the original time-stamps kept) so that a visitor can see the verifier refuse it. The league's real key never signs it: a checkpoint it signed over a changed log would be proof that the league's log equivocates. A stand-in key derived from the cycle's name signs the copy, and the copy is checked against a root that is the league's own with that key named as the log's. Everything is written to the output directory (the bundle, `forgery.json`, `trusted_root.json`) and the same cycle always gives the same bytes. Pass the directory to `site --forgery OUTDIR`: the copy is published under `forged/`, never among the bundles, and the page says what was changed and which key signed it.

```
{"findings": [{"name": "ledger_state", "ok": true, "detail": "OK"}, {"name": "segments", "ok": true, "value": 60}, ...,
 {"name": "backup", "ok": false, "detail": "no backup in .../backups"}], "ok": false}        (exit 1)
```

`status` signs nothing and saves nothing: a failure it finds is shown, and it is the league's own recovery that saves one. A league that has never run has no ledger, and `status` says so (exit 2) and makes none.

| Finding | Go to |
|---|---|
| `ledger_state` not OK | 8 |
| `segments` not readable | 8 |
| `heartbeat`: MISSING, UNFINISHED, LATE, ABANDONED | 9 |
| `checkpoint_lag` over its limit | 9 (no cycle has committed for a while) |
| `wal_bytes` over its limit | 10 |
| `free_bytes` under its floor | 11 |
| `backup` too old, missing, or an unfinished one left | 5 |

Run on loopback: yes, while the league ran, after a backup, and after a restore.

## 2. Start, stop, restart

```
sudo systemctl start hoshitori-league
sudo systemctl stop hoshitori-league          # SIGTERM: the league finishes the tick it is in, then exits 0
sudo systemctl restart hoshitori-league
journalctl -u hoshitori-league -f             # one JSON line per tick
```

The unit restarts the league 30 s after it exits 1. It does not restart it after exit 2, which means the settings cannot run: read the last line of the journal (`hoshitori-league: ...`), fix the settings or the file they name, then start it. A stop longer than five minutes ends with SIGKILL; that is safe (the ledger recovers on the next open, and a cycle in progress is closed as abandoned and its slots counted as lost on the next tick).

Run on loopback: the stop (SIGTERM to `hoshitori-league run`: exit 0 after its tick). `systemctl` was not run (no systemd on the build machine).

## 3. Take a backup now, and check one

```
PY -m hoshitori.backup take SETTINGS /var/lib/hoshitori/backups
PY -m hoshitori.backup verify /var/lib/hoshitori/backups/backup-NAME --trusted-root ROOT --anchor ANCHOR
```

`take` runs beside the league; there is no need to stop it. `ROOT` is a copy of the trusted root kept off the host, and `ANCHOR` a signed checkpoint kept off the host (a `.tlog-proof` file of a published bundle, or a `run/commitments/ID/note` copied off the host). Run on loopback, while the league ran:

```
{"anchors": 0, "captures": 10, "checkpoints": 1, "commitments": ["cmwom6js..."], "last_checkpoint": 47,
 "path": ".../backups/backup-TIME", "records": 60, "root": "98329f2e..."}        (take, exit 0)
{"anchors": 1, ... "records": 60, "root": "98329f2e..."}                                        (verify, exit 0)
```

A refusal is one sentence on standard error and exit 1, for example a backup file cut short:

```
hoshitori-backup: run/ledger/segments/segment-000000000000.jsonl is not the file the manifest lists: N bytes with SHA-256 ...,
the manifest says M bytes with ...
```

After a backup, copy it to the other host or account (not built: it depends on the provider), and keep at least the newest one there.

Run on loopback: yes (take, verify, and the refusals of a torn backup, a backup missing a segment and a backup of another log in `tests/test_restore_drill.py`).

## 4. Restore after losing the league folder or the host

1. On a new host, install as in `docs/DEPLOYMENT.md` sections 3 and 5, without starting the league.
2. Bring the newest backup from off the host into `/var/lib/hoshitori/backups/`, and from the places it is kept away from the host: the trusted root `root.json`, an anchor (the newest published `.tlog-proof` or commitment note), and the log key from escrow into `/etc/hoshitori/log.key` (owner `hoshitori`, mode `0400`).
3. The league folder must not exist (a restore writes only into a new folder; move an old one aside, never delete it):

```
PY -m hoshitori.backup restore /var/lib/hoshitori/backups/backup-NAME /var/lib/hoshitori/league \
   --trusted-root ROOT --log-key /etc/hoshitori/log.key --anchor ANCHOR
```

4. `LEAGUE status SETTINGS` (reads the restored ledger without writing; `"ledger": {"state": "OK"}`), then start the league and check the probe (section 1).

The restore verifies the backup first, then writes the folder and opens it with the ledger's and the capture store's recovery. It refuses: a target that exists; no anchor unless `--unanchored` is said; a log key the trusted root does not allow for the backup's size; a backup older than the anchor ("the backup holds N records, fewer than anchor 1 of size M: it is older than the anchor"); a backup of another history or another log. Records committed after the backup was taken are not in it: an anchor newer than the backup says so, and the league then needs a newer backup or a replica of the segments (section 8), never an older history.

Run on loopback, the folder removed while the backup and the key were kept apart:

```
{"anchors": 1, "captures": 10, "checkpoints": 1, "commitments": 1, "indexed_missing_rows": 0, "log_key": ".../league/log.key",
 "path": ".../league", "pending": 0, "records": 60, "replayed_intents": 0, "root": "98329f2e..."}        (restore, exit 0)
{"cycles": {"COMMITTED": 1}, "failures": 0, "forecasts": 6, "heartbeat": [], "ledger": {"detail": "", "state": "OK"},
 "records": 60, "settlements": {}, "slots": {"AWAITING_ORIGIN": 6}}                                     (status, exit 0)
```

`tests/test_restore_drill.py` does the same in the suite with backups taken while cycles commit: the restored ledger holds every record and checkpoint that existed when the backup began, its index equals one rebuilt from its segments, the bundles of its committed cycles verify at C3 with both verifiers, and the next cycle commits with a checkpoint consistent with the anchor.

## 5. A backup failed, or is too old

`systemctl status hoshitori-backup` and `journalctl -u hoshitori-backup` show the sentence. A `NAME.part` folder is the backup that failed; read it, then remove it by hand (the tools never remove it). The common causes: the ledger is not OK (section 8; a backup is taken only of a ledger that is OK), the disk is full (section 11), a file the settings name is missing. Take one by hand (section 3) once the cause is fixed.

## 6. The restore drill

```
PY -m hoshitori.backup drill --newest-in /var/lib/hoshitori/backups --scratch /var/lib/hoshitori/drill \
   --trusted-root /var/lib/hoshitori/league/root.json --log-key /etc/hoshitori/log.key --unanchored
```

The weekly timer runs this. It restores the newest backup into the scratch folder, reports what the recovery found and how many seconds it took, and removes the folder. Unanchored, it proves that the backup restores, not that it is the latest history. Run on loopback:

```
{"anchors": 0, "captures": 10, "checkpoints": 1, "commitments": 1, "indexed_missing_rows": 0, "pending": 0, "records": 60,
 "replayed_intents": 0, "root": "98329f2e...", "seconds": 0.053}        (exit 0)
```

## 7. Old backups

```
PY -m hoshitori.backup prune /var/lib/hoshitori/backups --keep 72
```

The backup unit runs this after every backup that succeeded. Only folders whose manifest is a backup's are counted; a `.part` folder, a link or another file is never removed. Run on loopback: yes (`{"removed": []}`).

## 8. The ledger is in INTEGRITY_FAILURE, or its segments do not read

The ledger refuses writes and stays that way until a person acts (ADR-026). Do not delete, truncate or edit any ledger file.

1. Stop the league. Copy the whole league folder aside.
2. Read the reason: `cat /var/lib/hoshitori/league/run/ledger/STATE` and the probe's `ledger_state` or `segments` detail.
3. If a replica of the segments exists whose root matches a signed or attested checkpoint, the ledger's `restore_from_replica` puts it in place and keeps the damaged segments in `quarantine/` (tested in `tests/ledger_checks.py`, scenario `replica_restore`). It has no command yet.
4. Otherwise restore the newest backup into a new folder (section 4, with an anchor), compare, and decide which history to run on. What was committed after that backup and is not in a replica is lost, and that is a decision for the owner, not for this runbook.

Not run as a procedure: there is no replica on the build machine.

## 9. Cycles missing, unfinished, late or abandoned

The probe's `heartbeat` names the slot. Nothing is repaired: a slot that passed while nothing ran is never made up, and the record shows it. Find why in `journalctl -u hoshitori-league` (the tick lines carry the cycle's state and reason):

- the league was not running (section 2);
- a venue did not answer (the cycle is abandoned with a reason; run the venue survey from this host, `docs/DEPLOYMENT.md` section 1);
- the time-stamp authorities did not answer in time (the cycle reaches C1 instead of C3, and says so);
- the clock moved back by more than a second: the ledger refuses commits (`ClockError` in the journal). Fix the time synchronisation (`timedatectl`), then restart.

Changing an oracle venue or an authority means a new protocol or a new trusted root, which is an owner decision.

## 10. The write-ahead log grows

A reader that keeps a transaction open stops SQLite from resetting the log ("checkpoint starvation", sqlite.org, write-ahead log section 6). The league's own reads are short, and the backup reads in one step. "When the last connection to a database closes, that connection does one last checkpoint and then deletes the WAL" (same page), so a restart of the league folds the log back into the database. In the loopback run the probe read 1,676,872 bytes while the league ran, and 0 after a stop and restore. If the log grows while nothing else reads the database, report it with the probe's line.

## 11. The disk is filling

The probe's `free_bytes` is under its floor. Prune backups (section 7) to a smaller number, once the newest is safely off the host. Never delete files in the league folder. If the ledger itself fills the disk, move to a bigger disk by a backup and a restore (section 4).

## 12. A new build, or back to the previous one

1. Install the new build beside the old (`/opt/hoshitori/app-NEW`, `/opt/hoshitori/venv-NEW`, as in `docs/DEPLOYMENT.md` section 3) and run the default test suite there.
2. Take a backup (section 3).
3. Stop the league, point the units at the new paths (a `systemctl edit` drop-in, or links), start it.
4. Check the probe (section 1). Export the newest committed cycle with `LEAGUE export` while the league is stopped and verify it offline.
5. To go back, stop the league, point the units at the old paths, start it. A build never changes the history; a build that changed the ledger's file format must say so and is not rolled back this way.

Not run: there is no host.

## 13. The log key is exposed

Stop the league. The ledger signs with one key: a new key needs a new era in the trusted root, signed by the root's keys, and the ledger opened with the new key refuses the checkpoints the old key signed (found while building the backup checks, `tests/backup_checks.py`, scenario `eras`). A rotation therefore needs design work as well as the owner's root keys. The old key cannot make time-stamp tokens dated before the exposure, which is what the independent tokens are for (LEDGER_SPEC section 5).

Not run: owner decision and missing design.

## 14. Not covered here

The RYO route (off) and the model arms (off in the template) have no procedure: they are not run by a deployed league. Abuse floods: the league opens no port. The static site's hosting: not built.

## On-demand forecasts

If the settings name an `ondemand` identity, each tick settles the on-demand forecasts that have matured and prints an `on_demand` member: `records` written, `pending` (waiting for candles), `deferred` (a venue gave no time) and `failed` (the reason a pass could not run, or none). `failed` set means the pass raised: the league's own cycle and settlement still ran. Run the tick again; the pass writes only what is new. A forecast whose candles never arrive ends as unsettleable after the retry window, as a league forecast does. If the settings name no identity, nothing settles an on-demand ledger by itself: run `scripts/ondemand_journey.py settle SETTINGS DIR`.


## Arm R's plans and the Desk on the tick line

A league that reads RYO prints a `planned` member on each tick: `records` (arm R plans settled), `pending` (a plan whose window has settled and cannot be decided yet) and `failed` (each read that could not be settled, with its reason). With `"desk": true` a tick that committed a cycle prints a `desk` member: `runs` (each run of this tick, its trigger, how it ended and the forecast it committed, if any), `raised` (a run that could not start, with the reason, for example a token the league's registry does not resolve to one instrument) and `failed` (the reason the step itself raised). A Desk failure never stops the league's own cycle. Nothing to do for a `raised` run: its event is not tried again on a later cycle, because each cycle's step reads only that cycle's events.
