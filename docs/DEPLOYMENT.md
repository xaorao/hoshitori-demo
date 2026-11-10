# Deployment (prepared, not deployed)

This describes how the league would run on one server. Nothing here has run on a server: no host, region or provider is chosen, nothing is deployed, nothing is pushed to a registry. What was run is listed under each part; everything else is marked not verified. The procedures for running it are in `docs/RUNBOOK.md`.

## 1. Choose the host by measuring from it

The host is not chosen until the venue survey has been run from each candidate machine. A venue can be blocked on one network and open on another (finding CF-60), and the protocol fixes two oracle venues for its whole life, so the host and the pair are chosen together, from evidence.

```
python -m hoshitori.venue_survey --watchlist BTC,ETH,SOL,XRP,ADA,DOGE,LINK --label "PROVIDER REGION SIZE"
```

For each candidate venue (`binance-spot`, `coinbase`, `hyperliquid`, `kraken`, `okx` by default) the survey reads the public spot instrument list through the gateway. It finds each token's instrument through the registry and the protocol's oracle rule (spot, quoted in USDT), so no instrument id is guessed from a symbol. It then measures the one-minute bar coverage of exactly those instruments over the last hour. A pair can settle when every token has its two instruments and both pass the coverage gate. The command exits 0 when at least one pair can settle every token and prints one JSON line. Keep that line with the label of the host. It needs no key and makes public reads only.

One real run from the machine this was built on gave this result. That machine is not a candidate host, so the result is an example only:

| Venue | List read | One-minute bars present in the last 60 minutes (7 tokens) |
|---|---|---|
| binance-spot | yes | 60 of 60 for each token, none padded |
| kraken | yes | 60 of 60 for each token; 21 to 53 of them padded (zero volume) |
| coinbase | yes | 0 to 7 of 60: cannot settle |
| hyperliquid | yes | no spot instrument quoted in USDT for these tokens |
| okx | no (`CALL_FAILED`: no complete answer) | not probed |

The usable pairs were `binance-spot,kraken` and `kraken,binance-spot`. On Kraken the registry joined the tokens to `XBTUSDT` and `XDGUSDT` (Kraken's names for BTC and DOGE), not to names built from the symbols.

Choosing the provider, the region and the pair is an owner decision (see the hand-off of unit B9). The survey decides between candidates; it does not choose them.

## 2. Shape

One always-on Linux virtual machine runs the league under systemd (OPERATIONS_ARCHITECTURE section 1). There is no network listener: the league only makes outbound calls (exchanges, time-stamp authorities). The static site (`hoshitori-league site`) is copied to a static host; that part depends on the provider and is not described here.

| Path | Owner, mode | Holds |
|---|---|---|
| `/opt/hoshitori/app` | root, read-only to the service | the reviewed repository tree of one build |
| `/opt/hoshitori/venv` | root, read-only to the service | Python 3.12 with the locked dependencies and the `hoshitori` package |
| `/etc/hoshitori/log.key` | `hoshitori`, `0400` | the log signing key; never in a backup (section 6) |
| `/var/lib/hoshitori/league/` | `hoshitori`, `0700` | the league folder: `settings.json`, `protocol.json`, `mapping.json`, `barrier.json`, `root.json`, and the state directory `run/` (`ledger/`, `captures/`, `commitments/`, `dispatch.jsonl`) |
| `/var/lib/hoshitori/backups/` | `hoshitori`, `0700` | the backups, one folder each |
| `/var/lib/hoshitori/drill/` | `hoshitori`, `0700` | the scratch folder of the restore drill, empty between drills |

Every file the settings name, except the log key, lives inside the league folder. A backup refuses settings that name a file outside their folder.

## 3. Build and install

These commands are the plan for a fresh host. They have not been run on a server. The two install commands were run on the build machine, into a scratch virtual environment. There the lock installed with every hash required, the package installed without dependencies, and `hoshitori-league`, `python -m hoshitori.backup`, `python -m hoshitori.host_probe` and `python -m hoshitori.venue_survey` all started.

```
sudo useradd --system --home-dir /var/lib/hoshitori --shell /usr/sbin/nologin hoshitori
sudo mkdir -p /opt/hoshitori /etc/hoshitori
sudo rsync -a --exclude .venv --exclude .env --exclude node_modules --exclude dist ./ /opt/hoshitori/app/
sudo uv venv --python 3.12 /opt/hoshitori/venv
sudo uv pip install --python /opt/hoshitori/venv/bin/python --require-hashes -r /opt/hoshitori/app/deploy/requirements.lock
sudo uv pip install --python /opt/hoshitori/venv/bin/python --no-deps /opt/hoshitori/app
```

`deploy/requirements.lock` pins the four runtime dependencies (`asn1crypto`, `cffi`, `cryptography`, `pycparser`) at the versions the test suite ran with. It carries the SHA-256 of every distribution PyPI holds for each version, and the same file serves x86-64 and arm64 Linux. `tests/test_deploy_files.py` fails if the lock and the tested versions differ. Keep the build's archive and its SHA-256 beside the host's records, so the running code can be named (OPERATIONS_ARCHITECTURE section 4).

### Serving the pages

The league makes only outbound calls; the pages are static files, served by any web server over HTTPS. Whatever serves them must send the
headers of `deploy/security_headers.json`: a Content-Security-Policy that allows nothing by default and only the site's own scripts, styles
and data (no inline script or style, no remote file, no framing, no base address, no form post), `X-Content-Type-Options`,
`Referrer-Policy`, `Cross-Origin-Opener-Policy` and `Strict-Transport-Security`. The page test serves every page under exactly these headers
and fails a page that needs more; `tests/test_security_headers.py` fails when the policy is weakened. What is not checked: that a given
host really sends them (read the response headers once the address exists), and the headers' effect in a browser other than the three the
page test runs.

## 4. Make the league

The steps are the quickstart's (`scripts/quickstart.py`), run as the service user, with the venues the survey chose.

1. The log key, made once and never copied by the product:
   `sudo install -d -o hoshitori -g hoshitori -m 0700 /etc/hoshitori`, then
   `sudo -u hoshitori /opt/hoshitori/venv/bin/hoshitori-league init-key /etc/hoshitori/log.key ORIGIN` (it writes the key readable by its owner only and prints only the public key), then `sudo chmod 0400 /etc/hoshitori/log.key`. A second copy is held away from the host (escrow); who holds it is an owner decision. Without the key a restored ledger cannot sign again.
2. The trusted root. For an exploratory run use `scripts/make_exploratory_root.py` as the quickstart does. A release root, and who holds its keys, is an owner decision. Keep a copy of `root.json` off the host: it is public, and a restore checks the backup against it.
3. The mappings, from history: `scripts/build_mapping.py` and `scripts/build_barrier_mapping.py`, as in the quickstart.
4. The protocol: `hoshitori-league make-protocol ... --primary VENUE --second VENUE`, with the pair the survey found usable.
5. The settings: copy `deploy/settings.template.json` to `/var/lib/hoshitori/league/settings.json`. Set `venues` and `lists` to the two venues of the protocol and `source` to the league's name. Every other value is the quickstart's. The template names the key by its absolute path, `/etc/hoshitori/log.key`, and holds no secret. `models` is `null`: the model arms need a model key in the environment (`model_settings` reads it from the variable it names). The systemd manual warns that "environment variables are not suitable for passing secrets" (systemd.exec, `Environment=`). So turning the model arms on waits for an owner decision (section 9). RYO is off unless the settings carry a `ryo` member; what turning it on costs and risks is in `RYO_ENABLE.md`, and it too waits for an owner decision (the metered probe).

6. Optional, the on-demand ledger. Forecasts that are not part of a scheduled cycle (a Desk call, or a call a person enters) are sealed, time-stamped and settled on a ledger of their own, so that they can never read as league forecasts. It needs an identity of its own: make a second key with another origin (`hoshitori-league init-key /etc/hoshitori/ondemand.key ONDEMAND_ORIGIN`), build a second trusted root for that origin and key with the same authorities (`scripts/make_exploratory_root.py`), and add to the settings `"ondemand": {"log_key": "/etc/hoshitori/ondemand.key", "trusted_root": "ondemand-root.json"}`. The service refuses to start if the origin or the key is the league's, or if that root does not name the key. With the member present the league's tick settles every on-demand forecast that has matured, once, and says what it did on the `on_demand` member of the tick line; a failure there is reported and never stops the league's own work. Without it nothing is opened and nothing is settled.

7. Optional, the Desk inside the league (needs item 6 and the model arms). Add `"desk": true` to the settings. On each committed cycle the Desk investigates the cycle's own events (an instrument a venue listed, removed or changed) whose price then moved at least twice its expected move over the shortest of 1, 4 or 24 hours that covers the event, the largest first. At most three runs start in a UTC day, a token at most once a day, an event never twice; the count is read from the Desk's own ledger (`DIRECTORY/desk`, kept under the on-demand identity), so a restart cannot reset it. Each run may ask RYO once and the model at most twice. A long call is sealed, time-stamped and settled on the on-demand ledger; an abstention or a failure is recorded as the run's result. The service refuses to start with `"desk": true` and no on-demand identity or no models. A plain price move with no event starts no run: the Desk's hypothesis is that the move began after the event, and without an event its tests have nothing to test.

## 5. The units

`deploy/systemd/` holds seven units. They were checked as text and by running their commands, never loaded by systemd (section 8).

| Unit | What it runs | When |
|---|---|---|
| `hoshitori-league.service` | `hoshitori-league run /var/lib/hoshitori/league/settings.json` | always; restarted 30 s after a failure (exit 1), never after exit 2 (settings that cannot run) |
| `hoshitori-backup.service` + `.timer` | `python -m hoshitori.backup take ...`, then `prune --keep 72` | every hour at half past |
| `hoshitori-health.service` + `.timer` | `python -m hoshitori.host_probe ... --backups /var/lib/hoshitori/backups` | every minute |
| `hoshitori-restore-drill.service` + `.timer` | `python -m hoshitori.backup drill --newest-in /var/lib/hoshitori/backups ... --unanchored` | Sundays at 03:30 |

```
sudo cp /opt/hoshitori/app/deploy/systemd/hoshitori-* /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/hoshitori-*.service /etc/systemd/system/hoshitori-*.timer
sudo systemctl enable --now systemd-time-wait-sync.service
sudo systemctl daemon-reload
sudo systemctl enable --now hoshitori-league.service hoshitori-backup.timer hoshitori-health.timer hoshitori-restore-drill.timer
```

What the units rely on, as the systemd manual pages describe it (freedesktop.org, "latest" pages):

- `Type=exec` reports a missing binary or user as a failed start. `Type=oneshot` counts the unit as up only after its process exits. `ExecStartPost=` runs only after a oneshot's `ExecStart=` succeeded, so a failed backup is never pruned against (systemd.service).
- `Restart=on-failure` restarts after a non-zero exit. `RestartPreventExitStatus=2` stops the restarts for the exit status `hoshitori-league` uses for settings that cannot run. `TimeoutStopSec=` is the wait between SIGTERM, the default first signal, and SIGKILL (systemd.service, systemd.kill). The league stops after the tick it is in. The five minutes it is given is a prior, not a measurement. A SIGKILL is safe: the ledger recovers on every open (process kills tested in `tests/test_ledger_kill.py`).
- `ProtectSystem=strict` makes the whole file system read-only except `/dev`, `/proc` and `/sys`; `StateDirectory=` creates and opens `/var/lib/hoshitori` (and the named folders below it) for the service user. The health unit has no `StateDirectory=`, so every path is read-only to it. `NoNewPrivileges=`, `ProtectHome=`, `PrivateTmp=`, `PrivateDevices=` and `UMask=0077` follow the manual's advice to turn on as much sandboxing as the service allows (systemd.exec).
- A timer starts the service of its own name. A run missed while the machine was off happens once at the next start with `Persistent=true`. A timer does not start a second run of a service that is still running (systemd.timer). Calendar expressions follow systemd.time (`minutely` is `*-*-* *:*:00`; `*-*-* *:30:00` is half past every hour).
- `After=time-sync.target` waits for a synchronised clock only when `systemd-time-wait-sync.service` is enabled (systemd.timer). The ledger refuses commits if the clock moves back by more than one second.

## 6. Backups

`python -m hoshitori.backup take SETTINGS INTO` backs up the league folder while the league runs. It never takes the writer's lock and never stops the league. It copies the settings and the files they name whole. It copies `checkpoints.jsonl` and `attested.jsonl`, then `journal.jsonl`, up to their last newline. It copies `index.db` with SQLite's online backup API in one step. It copies the segments (the newest up to its last newline), then the captures and every body they name, then every saved commitment, then the dispatch journal. That order makes the copy a consistent prefix of the log; the reasons are in the module's docstring (`src/hoshitori/backup.py`).

- SQLite's online backup makes "the destination a bit-wise identical copy of the source database as it was when the copying commenced". It does that in one step: an incremental backup "is restarted" whenever another process writes, and "may never run to completion" (sqlite.org, "Using the SQLite Online Backup API" and `sqlite3_backup_step`). A plain file copy of a live WAL database is not used. In a measured trial, 35 of 60 such copies would not open (OPERATIONS_ARCHITECTURE section 2).
- The copy of the index is set to rollback mode, so it is one file. Bytes 18 and 19 of an SQLite file are 1 in rollback mode and 2 in WAL mode (sqlite.org, file format, section 1.3.3), and a check reads them.
- A backup is written under `NAME.part` and its manifest last. It is verified, synced, and only then renamed. A folder with a backup's name is whole. A `.part` folder left behind means a backup failed. The health probe reports it, and it is kept for inspection.
- The log key is never in a backup. A restore asks for it, from escrow, only to open what it restored.
- `verify` needs only public material: the trusted root and anchors. An anchor is a signed checkpoint kept away from the host, either a `.tlog-proof` file of a published bundle or a commitment's `note` copied off the host. Without an anchor, an old or substituted backup that is consistent in itself looks like the latest one. So a restore refuses to run without an anchor unless `--unanchored` is said.
- Copying each new backup to another host or account depends on the provider and is not built (section 9). A backup on the same disk does not survive the loss of that disk.

Measured on the build machine, not on a target host: the drills of `tests/test_restore_drill.py` and the commands of the runbook (`docs/RUNBOOK.md`). They used a league on loopback (fake exchanges and fake time-stamp authorities through real sockets). Restore time at a realistic size and power-loss durability were not measured; macOS `fsync` does not flush the drive's cache.

## 7. Monitoring

`python -m hoshitori.host_probe SETTINGS --backups DIR` reads the league's files and nothing else. It opens no database, takes no lock, writes nothing and uses no network. It reports the ledger's state, whether the segments read cleanly, the heartbeat of the latest slots (a cycle missing, unfinished, late or abandoned), the records since the last checkpoint, the size of the index's write-ahead log, the free disk and the age of the newest backup. It exits 1 when one finding is bad, which puts the health unit in the failed state. `hoshitori-league status` does not replace it: `status` now also reads without a lock and writes nothing, so it can run while the league runs, but it reports what the ledger holds, not the checkpoint lag, the write-ahead log, the free disk or the age of the newest backup.

Every limit (64 MiB of write-ahead log, 1 GiB free, a backup at most two hours old, 1,000 records since a checkpoint, the latest two slots) is a prior, as every number of OPERATIONS_SLOS is, until a measured period replaces it. Where alerts go (`OnFailure=` of the health unit) is an owner decision. The checks from a second host that OPERATIONS_SLOS section 6 asks for (the page, the bundles) need the static site to be hosted. They are not built.

## 8. What was checked, and what was not

- Checked here: the backup, the health probe and the venue survey each pass their own scenario checks. Each check was first seen to fail against an empty module. Every hand-made mutant of the three modules was noticed. Their sweeps are written and not run (the main builder runs them). The two restore drills pass in the suite. `tests/test_deploy_files.py` checks every unit directive against the list read in the manual pages, and runs the commands of the backup, health and drill units as written, with the host's paths moved into a temporary folder. Every runbook command that needs no server was run on a loopback league.
- Not verified: `systemd-analyze verify` and a real systemd start (no systemd on the build machine); the sandboxing on a real kernel; `StateDirectory=` ownership; the stop time of a real tick; any host, network or region; restore time at a realistic size; power-loss durability; whether `EnvironmentFile=` values are visible over D-Bus (the manual's warning names `Environment=`).

## 9. Decisions left to the owner

The provider, the region and the oracle pair, chosen from the venue survey run on each candidate host. Where backups are copied off the host, and how long they are kept. Who holds the escrow copy of the log key and the keys of a release trusted root. Where alerts go. How the model key reaches the model arms if they are turned on. Whether a container image is wanted as well as the systemd units (for a provider that runs only containers). The options, their costs and a recommendation for each are in the B9 hand-off.

## Sources

SQLite: [online backup](https://www.sqlite.org/backup.html), [`sqlite3_backup_step`](https://www.sqlite.org/c3ref/backup_finish.html), [write-ahead log](https://www.sqlite.org/wal.html), [how to corrupt](https://www.sqlite.org/howtocorrupt.html) (section 1.2), [`VACUUM INTO`](https://www.sqlite.org/lang_vacuum.html), [URI file names](https://www.sqlite.org/uri.html), [file format](https://www.sqlite.org/fileformat2.html) (section 1.3.3); Python [`sqlite3.Connection.backup`](https://docs.python.org/3.12/library/sqlite3.html). systemd: [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html), [systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html), [systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html), [systemd.time](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html), [systemd.unit](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html), [systemd.special](https://www.freedesktop.org/software/systemd/man/latest/systemd.special.html).

## Static site on Vercel

The site is static, so it needs no build step on the host and no environment variable. `scripts/make_vercel_bundle.py WEB_DIST RECORD_DIR OUT_DIR` writes a folder with the built interface, an exported record under `data/` and a `vercel.json` that sends the headers of `deploy/security_headers.json` on every path. From that folder, with your own Vercel login: `npx vercel deploy --prod`. The league keeps running on its own machine (or the virtual machine above); the hosted site shows the record as it was exported, and is refreshed by exporting and deploying again. The page that says when the site was exported is part of the interface.
