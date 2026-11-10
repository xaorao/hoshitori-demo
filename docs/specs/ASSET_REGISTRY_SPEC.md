# Asset registry specification

Scope: asset, venue and instrument identity. Status: DESIGNED; every venue fact below was read from the venue's own documentation or observed live from this machine (fixture `fixtures/venues/venue_probe.json`, content hashes recorded in it). Product rules: contract section 15 (identity), section 7 (oracles), section 39 (journey identity chain). This file is the structure and the algorithm.

## 1. Two things that must never share an identifier

| Object | Answers | Key | Examples |
|---|---|---|---|
| **Token** (economic identity) | "what is this thing a news item, a post or RYO is talking about?" | `token_id` | Bitcoin; Solana; a specific ERC-20 contract |
| **Instrument** (tradable series) | "what price series do we score and fill against?" | `instrument_id` | Binance spot `BTCUSDT`; Binance USD-M perpetual `1000SHIBUSDT`; Hyperliquid perpetual `kPEPE`; Hyperliquid spot pair `@107`; Kraken `XXBTZUSD`; Coinbase `BTC-USD` |

The contract's `asset_id` is the pair that a forecast scores: `(token_id, instrument_id)` with the quote class and kind carried on the instrument. **Evidence about a token informs a forecast on an instrument only through a mapping with status `RESOLVED`.** A ticker string is never a join key.

## 2. Fields

`Instrument`: `instrument_id` (hash of venue, raw id, kind), `venue`, `raw_id` (the venue's own string), `kind` (`spot`, `perpetual`, `future`), `base_raw`, `quote_raw`, `quote_class` (`USD`, `USDT`, `USDC`, other), `multiplier` (decimal string; 1 normally, 1000 for the 1000x aliases), `settle` (contract kinds), `tick_size`, `lot_size`, `min_notional`, `status` (venue status mapped to `TRADING`, `HALTED`, `DELISTED`, `UNKNOWN`), `contract_expiry`, `source_snapshot` (capture id and hash), `valid_from`, `valid_to`.
`Token`: `token_id`, `display_name`, `chain_id` and `contract_address` where the token is a contract token, `aliases[]` (each with the source that asserts it), `status`, `resolution_evidence[]`, `valid_from`, `valid_to`.
`Mapping(token ↔ instrument)`: `token_id`, `instrument_id`, `multiplier`, `status` (`RESOLVED`, `AMBIGUOUS`, `UNKNOWN`), `evidence[]` (which sources agree), `valid_from`, `valid_to`.

All three are bi-temporal: a change never overwrites; it closes `valid_to` and opens a new row (the point-in-time rule of the domain model, section 2; Qlib's PIT store does the same with a publication date per row). A decision stores the registry snapshot hash it used.

## 3. What each venue shows (verified)

| Venue | Observation | Consequence |
|---|---|---|
| Binance spot | `exchangeInfo?symbol=BTCUSDT` returns `baseAsset BTC`, `quoteAsset USDT`, `status TRADING`, filter types (`PRICE_FILTER`, `LOT_SIZE`, `NOTIONAL`, ...) and `rateLimits` (`REQUEST_WEIGHT` 6,000 per minute); the request cost 20 weight (`x-mbx-used-weight-1m`); limits are per IP, a 429 carries `Retry-After`, repeated violations draw a 418 ban from 2 minutes to 3 days (documentation read); klines are identified by open time and the newest returned bar may be unfinished | tick, lot and notional filters come from the venue, never constants; an unfinished bar is excluded by comparing its close time with the venue's server time (`/api/v3/time`) |
| Binance USD-M futures | 936 symbols today; **`1000SHIBUSDT`, `1000PEPEUSDT`, `1000FLOKIUSDT`, `1000BONKUSDT` and others** with `baseAsset 1000SHIB`; the same bases also trade against USDC (`1000PEPEUSDC`) | a base asset beginning with `1000` is a multiplier alias: `multiplier = 1000`, price of one token = price / 1000; never join to the plain `SHIB` series without the multiplier |
| Hyperliquid perpetuals | 234 perpetuals; `k`-prefixed names (`kPEPE`, `kSHIB`, `kBONK`, `kLUNC`, `kFLOKI`, `kDOGS`, `kNEIRO`) are the 1000x series | the same rule with a different spelling; the table of aliases is data, not code |
| Hyperliquid spot | `spotMeta` lists 510 tokens and 347 pairs; the first pairs are `PURR/USDC` and then `@1`, `@2`, ... (`index`, `isCanonical`); the documentation says spot data requests use `PURR/USDC` for PURR and `@{index}` for the rest; HIP-3 perpetuals are prefixed with the deployer's name (`xyz:XYZ100`) | a spot instrument id is an **index into a versioned list**, so the mapping is valid only with the `spotMeta` snapshot it came from; a `dex:` prefix makes a different instrument from the unprefixed name |
| Kraken | `AssetPairs?pair=XBTUSD` returns `XXBTZUSD` with `altname XBTUSD`, `wsname XBT/USD`, `base XXBT`, `quote ZUSD`, `tick_size 0.1`, `status online` | `XBT` is Bitcoin; `XXBT` and `ZUSD` are Kraken's prefixed asset codes; both spellings must resolve to the same token and the pair to `quote_class USD` |
| Coinbase Exchange | `products/BTC-USD`: `base_currency BTC`, `quote_currency USD`, `quote_increment 0.01`, `base_increment 0.00000001`, status fields | `quote_class USD`; increments are decimal strings |
| OKX | `www.okx.com` does not resolve on this network (its name is blocked by the resolver; the earlier timeout was that block). `eea.okx.com` and `us.okx.com` answer with the same prices and a different instrument list: SPOT 1,213, SWAP 526, FUTURES 285 rows on `eea`, all mapped with none left unmapped; BTC-USDT spot has tick `0.1` and lot `0.00000001` | the second USDT oracle is reachable here through a regional host; which host a deployment uses is part of the venue's identity and is chosen with the hosting region (the target host still has to be probed) |
| CCXT (reference) | unified spot symbol `BASE/QUOTE`; swaps and futures `BASE/QUOTE:SETTLE` with a date suffix for dated futures; each market carries `type`, `spot`, `swap`, `future`, `linear`, `inverse`, `contract`, `settle`, `precision`, `limits` and the raw `info` | the shape of our `Instrument` follows this; CCXT is read for its model, not added as a dependency for execution (nothing here trades) |

## 4. Resolution algorithm

1. **Collect**: each venue adapter fetches its instrument list on a schedule (daily, and on any lookup miss, within the gateway budget) and stores the raw capture; a diff against the previous snapshot is a ledger event (new, changed status, delisted, renamed).
2. **Normalise** to `Instrument` rows using the venue's own fields. A pure function; golden fixtures per venue.
3. **Group** instruments into candidate tokens by `(chain_id, contract_address)` when a contract is stated, otherwise by a curated alias table (`XBT` → Bitcoin; `1000SHIB`/`kSHIB`/`SHIB` → Shiba Inu with multipliers), otherwise by base-symbol equality **within one venue only**.
4. **Status**: `RESOLVED` when the group has exactly one token, at least two independent sources agree on the grouping (for example a venue list and a chain or labelled-address source for contract tokens, or two venues for native coins), and no other token claims the symbol in the sources consulted; `AMBIGUOUS` when two tokens claim the symbol (a renamed or reused ticker, wrapped and native variants, same symbol on different chains); `UNKNOWN` otherwise.
5. **Join rule**: evidence items are merged only across mappings that are `RESOLVED`. For an `AMBIGUOUS` symbol the evidence is kept, shown unmerged and labelled, and the decision is made on the resolved subset or abstains with the reason `IDENTITY_AMBIGUOUS`.
6. **RYO symbols**: RYO tools take symbols and return `asset.chain` and `asset.contract` as `null` for symbol lookups (API compatibility, section 6). An RYO item is `RESOLVED` to a token only if the registry maps that symbol to exactly one token that the exchange sources also list; otherwise it is an `AMBIGUOUS` item and its evidence is quarantined from the join. Whether RYO's own symbol resolution matches ours is measured (a registry-versus-RYO agreement report) once the key exists.

## 5. Oracles, quote classes and basis (contract section 7)

- Pair A (USDT quote): Binance spot USDT is primary. Pair B (USD quote): Coinbase USD is primary and Kraken USD second; both answered today (`products/BTC-USD`, `AssetPairs`). A forecast uses one pair for its whole life.
- Server time for each oracle is read from the venue's own endpoint at settlement and recorded (Binance `/api/v3/time`, Kraken `/0/public/Time`, Coinbase `/time`: all answered today), and a bar counts as closed only if its close time is before that server time.
- **Basis**: the USDT/USD basis is measured at each settlement from a USDT/USD pair on a pair-B venue and stored; a basis beyond the pre-registered bound marks the forecast `BASIS_STRESS`. A price in one quote class is never compared with a price in another.
- **Delisting or renaming mid-life**: the forecast is `VOID` or `UNSETTLEABLE` with the registry event as the reason; it is never re-pointed to a successor symbol.

## 6. Adversarial join cases (the test set; each must end in the stated state)

| Case | Required outcome |
|---|---|
| `SHIB` evidence joined to `1000SHIBUSDT` prices without the multiplier | rejected: `IDENTITY_MISMATCH` |
| `kPEPE` price used as the price of `PEPE` | rejected |
| Hyperliquid `@107` resolved with a `spotMeta` snapshot of a different date | rejected: snapshot hash mismatch |
| Kraken `XBT` and Coinbase `BTC` | joined to one token, status `RESOLVED` |
| Kraken `XXBTZUSD` joined to a USDT price series | rejected: quote class differs |
| Perpetual funding evidence used for a spot forecast | labelled as a different instrument kind; never merged |
| A symbol that two sources assign to different contracts | `AMBIGUOUS`; evidence unmerged |
| Wrapped, bridged or venue-issued variants (for example a wrapped Bitcoin) treated as the native coin | separate tokens unless the registry says otherwise |
| An RYO symbol with `chain: null` and two candidate tokens | `AMBIGUOUS`; RYO evidence quarantined |
| A symbol delisted and later reused by a different token | new token row; old mappings closed; no automatic carry-over |

## 7. Failure modes and invariants

- A venue list that fails to parse, shrinks by more than a set fraction, or returns an empty universe is rejected as a snapshot and the previous one stays current with an `age` shown (a half-empty list must not delist everything).
- Invariants: every `asset_id` on a record resolves to exactly one `(token, instrument)` in the registry snapshot the record names; a mapping row's `valid_from` precedes the first record that uses it; no record uses a mapping with status other than `RESOLVED` (except the explicit `AMBIGUOUS` quarantine path).
- Connection to the journey: the claim node's asset, the evidence items, the forecast, the oracle series and the fill all carry the same `asset_id` (contract section 39); the registry is the only component allowed to create one.

## 8. Not verified

OKX reachability and a replacement second USDT oracle; the data-use terms of each venue for storing and republishing candles and instrument lists (Binance terms read at the title level only); CAIP-19 identifiers as the token key (not studied); the names of tokens in every alias table (curated by hand and reviewed, not generated).


## Rule from an earlier study (reproduced)

The mapping key is the venue's own pair id and token index, never a library's unified symbol. On a recorded Hyperliquid response CCXT skipped 41 of 347 spot pairs, labelled three markets with a wrong base token, put two pairs on one symbol and unified the wrapped asset UBTC to `BTC/USDC`. A pair a library skips is `UNMAPPED`, not absent. Mutants: a wrapped asset joined to its native asset; two pairs on one symbol; a mislabelled base.

## 9. As built (build unit 4.8; where the code differs from, or settles, what is written above)

Code: `registry.py` (state machine, resolution, joins), `registry_aliases.py` (curated spellings), `ids.py` (natural-key ids), `venue_data.token_symbol` (the multiplier prefix taken off a base), `Instrument.base_ref` (the venue's own token identity; Hyperliquid spot sets the token id). Tests: `test_registry.py` (scenario checks and a sweep of every small change of the code: 915 places, 5 harmless ones each explained), `test_registry_live.py` (the nine recorded lists), `test_ids.py`.

- **Tokens and series.** A token is a symbol and a series: `natural_id("tk", {symbol, series})`. A symbol that was seen to end (its last live listing delisted or removed) and then listed again is series 2, with its own mapping history and no carry-over; a name first seen delisted and later listed elsewhere stays in series 1, because it was never seen to end. An instrument that leaves and returns, or whose pair id is re-pointed at another token, is a new *generation* of the same instrument id.
- **Status.** `RESOLVED` needs no conflict and at least two independent sources: two *operators* (the part of the venue name before the first hyphen, so Binance spot and Binance USD-M are one), or one listing and a contract claim. The basis is stored with the status and is `CONTRACT`, `CURATED`, or `VENUES`; `VENUES` is the weakest (the same ticker on two exchanges) and is shown as such. Conflicts, in this order: two contracts claimed for one symbol (`CLAIM_CONFLICT`), two token ids of one name on one venue (`REF_CONFLICT`), one pair under two ids on one venue and kind (`DUPLICATE_PAIR`; dated futures are exempt). `UNKNOWN` is one source (`SINGLE_SOURCE`) or none (`NO_LISTING`). A delisted instrument keeps its mapping but is not a source.
- **Curated spellings are by operator and are never applied to text from outside.** A rule on the first letter of Kraken's codes would turn `XION`, `XAUT` and `ZAMA` into other tokens; the table lists the ten legacy codes the recorded answer shows (`XXBT`, `XXDG`, `XETH`, `XETC`, `XLTC`, `XMLN`, `XXLM`, `XXMR`, `XXRP`, `XZEC`), each confirmed by the `wsname` spelling in the same answer, and Bitcoin and Dogecoin also by price against Binance. The plain `XBT` and `XDG` never appear as a `base` and are not listed. A symbol from RYO or a post is only folded to upper case. Wrapped and bridged names (`WBTC`, `UBTC`) stay tokens of their own.
- **Admission.** A list is rejected, and the previous one stays current with its age, when it is empty, has a duplicate id, an instrument of another venue or outside its scope, an instrument already owned by another scope of the venue, has lost more than 20 percent of a list of at least 10, or carries more unmapped entries than the larger of 10 and 5 percent. Scopes are `all`, `spot`, `perpetual`, `future`. A rejection is a record. A renamed pair is a removal and an addition; no rename is guessed.
- **Records and the snapshot hash.** Everything the registry knows is a canonical record (`{"spec", "known_at", "events"}`) for the ledger, and `Registry.replay` rebuilds it. The snapshot hash is a hash chain over the records, so it names a history; `head_at(time)` and `snapshot_time(hash)` map between them. Every record has its own millisecond: a time at or before the last record is moved to a millisecond after it. A registry that fails to apply a record refuses all further use and must be rebuilt.
- **Joins.** `join(instrument_id, Want(...), as_of)` answers `OK` with an `Asset` (`asset_id` = the token and the instrument), or names the field that does not match: `IDENTITY_MISMATCH` (token, kind, quote class, or the multiplier the caller applied), `IDENTITY_AMBIGUOUS`, `IDENTITY_UNRESOLVED`, `SNAPSHOT_MISMATCH` (the snapshot hash the evidence was taken under never existed, or the instrument means something else now than then), `UNKNOWN_INSTRUMENT`. The ten adversarial cases of section 6 are tests.
- **Found on the real lists.** 1,943 token symbols over 7,242 mappings; 749 are `RESOLVED`, one `AMBIGUOUS`: `CAT`, because Binance lists `CATUSDT` and `1000CATUSDT` on the same venue and kind (two tokens, or one token twice, and the registry cannot tell). The first record of a venue is large (up to about 0.5 MB for 1,400 instruments); the ledger writer decides where it is stored.
- **Limits.** Two exchanges agreeing on a ticker is weak evidence of the same token (basis `VENUES`); no chain or labelled-address source is connected yet, so a contract claim comes only from what a caller supplies. Case is folded (no two spellings in the recorded lists differ only by case). The registry knows when *we* learned a fact, not when the venue changed it. Not independently reviewed.
