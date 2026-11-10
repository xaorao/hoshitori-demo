# Rules arm `L0`: the deterministic baseline

Scope: controlled league. Status: BUILT and TESTED as `features`, `rules_arm` and the collector's two default windows; the history job exists and has produced one exploratory mapping, which no protocol pins. Product rules: contract sections 1, 2, 5, 6, 9 and 20; `LEAGUE_EXPERIMENT.md` sections 1 to 3. Written before the code, as the plan requires; every choice below is a prior to be pre-registered with the protocol, not a result.

## 1. What it is for

`L0` answers one question for the league: what does a fixed, transparent rule with a frozen mapping to a historical base rate achieve on the same evidence, the same clock and the same scoring as the model arms? If a model arm cannot beat it, the model added nothing. It is not meant to be good; it is meant to be fixed, cheap, reproducible and hard to accuse of tuning. It reads observations only, never an RYO interpretation, and it needs no model and no RYO tool.

## 2. What it reads

Two candle series per candidate, collected through the gateway from the candidate's oracle instrument (`collect`): one-minute bars (120) and one-hour bars (40). The arm never reads bars directly: it reads one derived `features` item per candidate (kind `DERIVED`, transform `features` version 1, parents the two series items), built by `features.py` from the parsed series in exact decimal arithmetic. A feature that cannot be computed (a missing or unfinished bar in the span it needs) is not in `fields`; it is in `unknown_fields` with reason `ABSENT`, and nothing downstream may read it as a number.

| Feature | Definition |
|---|---|
| `close` | close of the last closed one-minute bar |
| `ret_1h` | `close / close 60 minutes earlier - 1` on the one-minute series (both bars present) |
| `ret_4h`, `ret_24h` | last closed hourly close over the close 4 and 24 hourly bars earlier, minus 1 |
| `atr14_share` | mean of the true range of the last 14 hourly bars, divided by the last hourly close; the true range of a bar uses the previous bar's close, so 15 contiguous hourly bars are needed |

The series, the bars and the arithmetic are the exchange's and ours; none is an interpretation. The features item inherits the exchange family, so independence counts treat it as one origin.

## 3. The rule

For a horizon `h` of 1, 4 or 24 hours: `z = ret_h / (atr14_share * sqrt(h in hours))`, so `z` is the move so far in units of the move one expects over `h` from recent hourly range. `z` falls in one of five states: `S1` for `z <= -1`, `S2` for `-1 < z <= -0.25`, `S3` for `-0.25 < z < 0.25`, `S4` for `0.25 <= z < 1`, `S5` for `z >= 1`. The square root is the correctly rounded decimal square root at 28 digits, so the state is the same in every process.

## 4. The frozen mapping

A mapping gives, per horizon and state, `n` (independent origins), `ups` and `ties`. The probability is `p = (ups + 1) / (n + 2)` (the posterior mean under a uniform prior), clipped to [0.01, 0.99]. A cell with `n` below `MIN_CELL` (a prior of 100) is unmapped. The mapping is a canonical record with its calibration metadata (venue, quote class, instruments, interval, period start and end, the horizons, the rule version, the number of origins skipped for missing bars) and its SHA-256; that hash is part of the arm's `agent_version_hash` and is pinned by the protocol before a scored period. It is computed once, on an exploratory period that ends before the first scored cycle, and never refitted inside a scored period (contract sections 9 and 20).

**Calibration.** From hourly bars of a universe of instruments: an origin is an hour boundary; the features are computed from the hourly bars at that boundary by the same function that the live arm uses (the live one-hour return is the hourly return when the origin is an hour boundary, so the two agree there); the outcome is whether the close `h` hours later is above the close at the origin (equal is a tie, counted and excluded). Walking each instrument from its oldest bar, a bar is an origin if the features can be taken at it, the bar `h` hours later exists and the hourly range is not zero; the next origin is at least `h` hours later, so cells count non-overlapping windows. A bar that cannot be an origin is counted in `skipped` (the first bars of a history, which have no past, are among them). The calibration is therefore exact at hour boundaries and an approximation at other minutes; the live minute origin differs from the hour boundary by up to 59 minutes of phase. This is stated, and the pre-registered check compares the live outcomes of `L0` by minute of the hour once enough exist.

## 5. What it decides

* **Probability.** `p` of the mapped cell for the candidate's horizon, as contract D's forecast.
* **Action.** `LONG` if `p >= 0.5 + EDGE` (a prior of 0.02), otherwise `FLAT`. Long-only: there is no short, and a `FLAT` is not an abstention (the probability is still committed and scored).
* **Abstention.** A voluntary `NO_CALL` with reason `UNMAPPED` when the cell has too few origins (and `NO_RANGE` when the hourly range is zero). The lifecycle records both as `OUT_OF_SCOPE`, its closed reason for a state the frozen mapping does not cover; the cycle's result keeps the arm's own word (CF-52). Nothing else is a voluntary abstention.
* **Failure.** A feature the rule needs is unknown, or the features item is not in the snapshot: the slot is `FAILED_EVIDENCE`. The arm never fills a gap with a default.
* **Invalidation.** One typed line, `close below entry minus 1.5 ATR14`, stored with the forecast and not scored (contract section 1).
* **Evidence.** The features item and the two series items it stands on, with the observation-set hash of what the reader gave it.

Contract B (the barrier forecast) is not part of the first version; it is planned as a second step of `L0` (section 5b), because calibrating a first-touch rate needs one-minute high and low history, and the hourly approximation would hide the same-candle ambiguity that contract B exists to handle.

## 5b. Contract B for `L0` (built and tested)

* **What it forecasts.** For a LONG side, whether the price reaches `P0 + k_target x ATR14` before `P0 - k_stop x ATR14` within the horizon, or neither (contract section 3); `P0` is the close of the candle that closes at the origin, `ATR14` is the mean true range of the last 14 closed hourly bars at the snapshot, `k_target = k_stop = 1.5` (priors). It is the same baseline spirit as the directional arm: no features beyond the ATR, the probabilities are the **frozen first-touch frequencies of the horizon**, `(p_target, p_stop, p_timeout)`, and a cell with fewer than `MIN_CELL` settled windows is `UNMAPPED`.
* **Calibration (5.12a, `barrier_rates`).** From the hourly and one-minute bars of a universe: an origin is an hour boundary; `P0` is the close of the minute bar that opens one minute before it; the barriers are set from the hourly ATR14 taken at the same boundary; the minute bars of the horizon are walked in order and the first minute that touches a level decides; a minute that touches both is `AMBIGUOUS` and is counted apart and left out of the probabilities (contract section 3, no finer data); a window with any missing minute, or with no ATR, is skipped and counted; the next origin of an instrument is at least one horizon later, so windows never overlap. The result is a canonical file with its hash, like the directional mapping, and is exploratory until a protocol pins its hash.
* **Probabilities.** Settled windows only: `p_target` and `p_stop` as their shares rounded to six places, `p_timeout` the remainder, each clipped to the floor and ceiling of section 5 and summing to exactly one.
* **The live arm (5.12b).** Takes the ATR from the same function as the calibration, the mapped probabilities for the horizon, and emits contract B forecasts beside the directional ones; the invalidation line is the same; `UNMAPPED`, a missing ATR (`FAILED_EVIDENCE`) and the rest follow section 5.
* **Settlement (5.12c, 5.12d).** `settle_job` reads, for a barrier forecast, the oracle's one-minute bars of the whole horizon (at most 1,440 for 24 hours, in pages the venue allows) once per instrument and minute, and `settlement.settle_barrier` decides; an ambiguous minute is refined with finer data only where a venue gives it, otherwise it stays `AMBIGUOUS` and is counted. The forecast's lifecycle and the scoring (three-class Brier on the settled classes) follow contract sections 3 and 7.
* **Weaknesses, stated.** A horizon-only first-touch frequency ignores the state; the hour-boundary origin is an approximation of a minute origin; the ambiguity share on one-minute bars can be material and is reported, never hidden.

## 6. Tests the build must pass

Known values for each feature on hand-made bars; every state edge on both sides; the square root and the state independent of the process; unknown features never become a number; **prefix invariance** (the decision from a series cut at a time equals the decision from a longer series cut at that time, so no later bar can enter); **planted future** (a bar that closes after the venue's clock, or one that has not closed, changes nothing); the independent reader gives the arm no interpretation; calibration on a hand-made history equals a brute-force count; calibration is order-independent; the mapping hash changes with any cell, any metadata field and the rule version; an unmapped cell gives `NO_CALL`; a missing feature gives `FAILED_EVIDENCE`; a mutation sweep of every module.

## 7. Weaknesses, stated

Five coarse states discard information; a momentum-only rule is one of many baselines; hourly calibration with a minute-phase live origin is an approximation; one exploratory regime may not describe the scored one; an arm whose probabilities hover near 0.5 will look unremarkable, which is what a baseline should do. The league's references (the constant 0.5 and the climatological rate) are scored beside it, so a reader can tell whether `L0` carried any information at all.
