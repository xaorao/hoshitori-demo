# The public record

`record.tar.gz` is an export of the running league: the site documents, one verification bundle for each committed cycle, the trusted root and a forged copy of one bundle that the verifier refuses.

`signals.json` in it is one collection of public voices and of 40 large Hyperliquid wallets for the Voices and whales page, made after the export with `hoshitori-league signals` from `signals.settings.json` (the wallets were chosen by `scripts/make_whale_cohort.py`, whose rule and snapshot digest are recorded in that file). No agent reads it and no cycle commits it.

Unpack: `mkdir -p ../record && tar -xzf docs/record/record.tar.gz -C ../record`

Check without installing anything: `python3 scripts/resettle_published.py ../record` (re-settles every published up-or-down call from Binance's public candles; add `--binance https://data-api.binance.vision` if the main host is not reachable).

Check a cycle's bundle: see "Verify a record offline" in the top-level README. The file is rebuilt by `scripts/pack_record.py`, which refuses to pack an export that holds a credential-looking string.
