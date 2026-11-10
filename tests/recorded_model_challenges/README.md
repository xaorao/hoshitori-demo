# Recorded model-arm challenges (fixtures)

Recorded, not made up. `BTC.json`, `ETH.json` and `SOL.json` are the C1 live challenge of the single-judgment arm (L1, prompt `l1-v5`,
temperature 0.7, 20 pairs, the source `binance-funding` taken out, one-hour forecasts of cycle `cymsmyj5ihfp3dzsfqr7hdbustpx`), written
again through `hoshitori.model_challenge_file.record` from their offline replay against the league's capture store
(`scripts/challenge_live.py SETTINGS --replay STORED.json --out FILE`). In that replay every run's action equalled the live file's, and
for ETH and SOL every stored request digest and capture as well (the BTC live file was written before the script stored the calls).

They hold ids, digests, seeds and the arm's own codes only: no prompt, no answer text, no key and no path. The checks that read them are
`tests/model_challenge_file_checks.py` (`check_recorded`).
