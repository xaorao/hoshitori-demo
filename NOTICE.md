# Notices and disclosures

This file discloses the libraries, data and specifications this project uses, with their licences. No source file has been copied from another repository. A few modules rebuild or adapt published methods, and a few reference values are copied from test data; each one is listed under Adapted code and reference values.

## Runtime dependencies

* Every package under `skills/`: none. Python standard library only.
* `src/hoshitori`: `cryptography` (Apache-2.0 OR BSD-3-Clause), used for Ed25519 signatures and for X.509 and RFC 3161 verification, with its own dependencies `cffi` (MIT-0) and `pycparser` (BSD-3-Clause); `asn1crypto` (MIT), used to read the ASN.1 structures of RFC 3161 time-stamp responses and CMS signed data, which `cryptography` does not parse.
* `verifier-js`: Node.js standard library only.

## Interface (`web/`)

* Shipped in the built pages: `react` and `react-dom` 19.3.1 with `scheduler` (all MIT). The Verify page loads, only when a person asks for a check, `@noble/hashes` and `@noble/curves` 2.4.0 (MIT; SHA-2 and Ed25519, P-256, P-384 and P-521 signature checks) and `buffer` 6.0.3 (MIT). Nothing else is bundled; fonts are the system's.
* Build and test only, not shipped: `vite` 8.3.4, `@vitejs/plugin-react` 6.1.2 and their dependencies (`rolldown`, `postcss`, `lightningcss` (MPL-2.0, unmodified), `picomatch`, `tinyglobby`, `fdir`, `nanoid`, `picocolors`, `source-map-js` (BSD-3-Clause), `detect-libc` (Apache-2.0), `@oxc-project/types`); `typescript` 6.0.3 (Apache-2.0); `@types/react`, `@types/react-dom` and `@types/node` (MIT); `axe-core` 4.10.2 (MPL-2.0), used by the accessibility checks of the page tests. Versions are pinned in `web/package.json` and `web/package-lock.json`.

## Development and test dependencies

* `pytest` (MIT), `jsonschema`, `ruff` (MIT) and `mypy` (MIT): standard tooling.
* `hypothesis` (MPL-2.0): property and stateful tests. Development only; not distributed with the package.
* `TA-Lib` (BSD-3-Clause) with `numpy`: a test-time reference used to check the indicator mathematics to 1e-9. Optional (`pip install .[reference]`); the skill does not import it.
* `playwright` (Apache-2.0) and the browsers it installs: used by the page tests in `web/tools`. Installed separately; not a dependency of the package.

## Test data and specifications

* `tests/vectors/merkle_reference.json` (field `ct`): leaf inputs, root hashes and proofs of the Certificate Transparency reference tree, copied from the test data of `github.com/transparency-dev/merkle` (Apache-2.0, commit c39fbf3). The accept and reject verdicts recorded beside each proof were produced by running that Go library as an oracle; none of its code is included.
* `tests/vectors/signed_note_spec_example.json`: the example verifier key and signed note from the C2SP signed-note specification v1.1.0 (https://c2sp.org/signed-note, CC BY 4.0, commit fea9004). The format rules are implemented from the specification text.
* `tests/vectors/canonical_record.json`: vectors written for this project, including the examples of RFC 8785 section 3.2 and hostile inputs.
* RFC 8785 and RFC 3161 are implemented from their text.
* The shape of the prompt-injection suite in `src/hoshitori/injection.py` (user tasks, attacker goals, attack families, and utility and attack-success predicates judged on the outcome) follows AgentDojo (MIT; Debenedetti, Zhang, Balunovic, Beurer-Kellner, Fischer and Tramer, NeurIPS 2024; commit 089ed468). The attack wording is this project's own; no AgentDojo code or data was copied.

## Data

* `skills/indicator-condition-check/tests/fixtures/*.json`: unmodified public market-data responses from Binance, Coinbase Exchange, Kraken and Bybit, recorded in October 2026 for offline tests. They are labelled as recorded wherever they appear.
* `data/operator_registry.json`: which organisation runs each time-stamp authority, with the public record that says so (the public CA database and the operators' own pages). Written for this project.
* `src/hoshitori/unicode15.py` and `verifier-js/src/unicode15.mjs`, written by `scripts/make_unicode_table.py`: the ranges of code points assigned in Unicode 15.0, derived from the Unicode Character Database 15.0.0 as compiled into CPython 3.12's `unicodedata` module. Only the ranges are reproduced, not a data file. The Unicode Character Database is copyright Unicode, Inc. and is published under the Unicode License. The notice reproduced below is the Unicode License V3 text distributed with Node.js; the licence page of the Unicode Consortium was not consulted, so confirm that this text covers the 15.0.0 data files before redistributing.

  > UNICODE LICENSE V3. COPYRIGHT AND PERMISSION NOTICE. Copyright © 2016-2025 Unicode, Inc. NOTICE TO USER: Carefully read the following legal agreement. BY DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING DATA FILES, AND/OR SOFTWARE, YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE TERMS AND CONDITIONS OF THIS AGREEMENT. IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE THE DATA FILES OR SOFTWARE. Permission is hereby granted, free of charge, to any person obtaining a copy of data files and any associated documentation (the "Data Files") or software and any associated documentation (the "Software") to deal in the Data Files or Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, and/or sell copies of the Data Files or Software, and to permit persons to whom the Data Files or Software are furnished to do so, provided that either (a) this copyright and permission notice appear with all copies of the Data Files or Software, or (b) this copyright and permission notice appear in associated Documentation. THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF THIRD PARTY RIGHTS. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS NOTICE BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL DAMAGES, OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA FILES OR SOFTWARE. Except as contained in this notice, the name of a copyright holder shall not be used in advertising or otherwise to promote the sale, use or other dealings in these Data Files or Software without prior written authorization of the copyright holder.

## Services used at run time

* Without a key: public candle endpoints of Binance, Kraken and Coinbase; public RFC 3161 time-stamp authorities (DigiCert and GlobalSign).
* With a key the user supplies: an OpenAI-compatible model endpoint. The example claims were read through NVIDIA's endpoint; that endpoint's terms for the data sent to it have not been reviewed.

## Adapted code and reference values

* `src/hoshitori/anytime.py` rebuilds, in Python with the standard library only, the empirical-Bernstein confidence sequence and the sub-exponential e-process of Choe and Ramdas, *Comparing Sequential Forecasters* (arXiv 2110.00115v6), and the gamma-exponential mixture boundary of `confseq`, following `github.com/yjchoe/ComparingForecasters` (`comparecast/confseq.py`, MIT, Copyright (c) 2021 Yo Joong Choe and Aaditya Ramdas, commit 52748c8) and `github.com/gostevehoward/confseq` (`src/confseq/uniform_boundaries.h`, MIT, Copyright (c) 2021 confseq developers, commit 5ffe733). Both licences permit use, copying and modification with their notices; the algorithm and its constants follow those files, and no file is copied whole.
* `skills/exit-liquidity-check` (`model.py`, `registry.py`, `venues.py`, `skill.py`) adapts the exit-cost model, the verdict bands, the confidence rule and the registry of canonical contracts of `hanko/skills/exit_liquidity/` in `github.com/IamHarrie-Labs/hanko` (MIT, Copyright (c) 2026 Harrie; read at commit 0a27f29). The code is rewritten for this package (standard library only, its own transport, parsers and tests) and corrected where the source was weaker (quote-side and junk-quote pools, fixed refusals); no file is copied whole. The recorded replies in `skills/exit-liquidity-check/tests/fixtures/` are unmodified public responses of DexScreener and Jupiter.
* `skills/evidence-delta` takes the idea of comparing two reads of one RYO tool while keeping nulls and gaps out of "unchanged" from the `evidence_delta` skill of a public entry in the same event (`Victork19/scarbook`, no licence: idea only; no code or wording was used).
* `tests/fixtures/anytime_confseq_golden.txt`: reference values computed by `confseq`'s own C++ code at the same commit and used to check the rebuild.

## Behaviour adapted from studied code

* Hanko (`IamHarrie-Labs/hanko`, MIT): the keyless Bluesky `getAuthorFeed` read with reposts and quotes marked as echoes, and echo detection by Jaccard similarity with containment for long posts inside a time window, the earliest statement keeping its independence. `src/hoshitori/voices.py` and `src/hoshitori/social_gateway.py` are written from that behaviour with the changes their docstrings state; no file was copied.
* Hyperliquid's public `/info` `clearinghouseState` call is read as the venue documents it; no SDK code is used.
* `skills/evidence-sensitivity/src/evidence_sensitivity/server.py` is this project's own `address_flag_check` server adapted to one tool. Removing or degrading evidence to see whether a decision changes is documented by Tracekit `why` (MIT) and Counterfact (Apache-2.0); no code from either is used.

## Design references

The following were read while designing the system. Nothing from them is included; any code adapted in future is listed here with its file, origin and licence.

* MIT: `langchain-ai/deepagents`, `anthropics/claude-agent-sdk-python`, `microsoft/agent-framework`, `microsoft/RD-Agent`, `NousResearch/hermes-agent`, `AI4Finance-Foundation/FinRL`, `AI4Finance-Foundation/FinGPT`, `Cygnux-Labs/Tracekit`, `virattt/ai-hedge-fund`, `HKUDS/Vibe-Trading`.
* Apache-2.0: `AI4Finance-Foundation/FinRobot`, `TauricResearch/TradingAgents`, `hummingbot/hummingbot`, `sigstore/rekor`, `transparency-dev/tessera`.
* BSD: `Metaculus/metaculus` (BSD-2-Clause).
* AGPL-3.0, ideas only and re-implemented from a written specification without code, layout or copy: `koala73/worldmonitor`, `rotki/rotki`.
* LGPL-3.0, not used: `opentimestamps/python-opentimestamps`, `nautechsystems/nautilus_trader`.
