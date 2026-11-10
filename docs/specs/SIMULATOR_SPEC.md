# Simulator and settlement arithmetic specification

Scope: forecast contract arithmetic, practice execution, risk kernel. Status: DESIGNED; the origin and settlement arithmetic of contract D was built twice and run (E5). The normative rules are the contract (sections 2 to 5, 7, 14, 17, 25); this file is the executable form: pure functions, their inputs and outputs, the property tests, and the independence requirement for the second implementation. Sources studied: NautilusTrader's fill-model documentation (LGPL project; documentation read, ideas only), Freqtrade's look-ahead analysis method (GPL; documentation read, ideas only), FinRL (MIT; known same-bar and NaN faults noted in the landscape study), the Binance klines documentation (candles identified by open time; the newest bar may be unfinished).

## 1. What the simulator is

A deterministic, side-effect-free library: `(decisions, candle series, cost parameters, rule versions) -> (fills, marks, book states, outcomes)`. It never calls the network, reads the clock, draws random numbers or reads a file. Candle series are read from the ledger-backed store by explicit key, so every result can be recomputed from stored inputs and the rule version. It makes no claim about real execution; every output table carries the list of what is not modelled (contract section 4).

## 2. The three clocks as functions

All times are integer milliseconds since the epoch (UTC). A 1-minute candle with open time `o` closes at `o + 60 000` (exclusive end); the oracle's `closeTime` field in the venue's own response (for example Binance's `openTime + 59 999`) is never used as the boundary.

```
eligible_after(attested_at, u_const, spread) = attested_at + u_const + spread
T0(ea)         = (floor(ea / 60000) + 1) * 60000                      # first boundary strictly after ea
P0             = close of the candle with open time T0 - 60000
authorized_at  = eligible_after                                       # level-dependent (contract section 4)
earliest_fill  = max(authorized_at + L, T0)                           # L default 15 000 ms
P_h            = close of the candle with open time (T0 + horizon) - 60000
```

**E5 evidence.** The closed form `T0` was checked three ways: against a brute-force scan of a candle list, against a separately written JavaScript implementation (exact integer and exact decimal arithmetic, ties rounded to the oracle tick), and over a sweep of every 250 ms position inside a minute, for several `u` values and minutes, including the first and last minutes of a day: 6,804 cases, 0 violations of "`T0` is minute-aligned, strictly after `eligible_after`, and at most one minute later". The contract's worked example (tokens at 10:01:02 and 10:01:04, `u` = 5 s plus 2 s spread) yields `eligible_after` 10:01:11 and `T0` 10:02:00. A commitment attested exactly on a minute boundary with `u = 0` gets `T0` one minute later (the candle closing at the boundary is not eligible). A 20,000-case differential fuzz (random ticks from 1e-8 to 1, gaps in 3 percent of candles, planted candles after the horizon, forced ties) produced 0 differences between Python and JavaScript. Both implementations were written by the same author: they check the arithmetic, not the reading of the contract, so the independent second implementation of the contract (section 7) remains required.

**Resolution of the time source.** A time-stamp token's `genTime` has whole-second resolution (0 of about 100 live tokens carried a fraction; E2b), so `attested_at` is only known to within a second; the pre-registered `u_const` (default 5 s) must cover it. The measured excursions of `genTime` against our request midpoint were between about −0.9 s and +0.9 s for the quick authorities and up to +1.9 s for one slow responder (round trip 3 s); this is evidence about authorities, not a proof (TA-3 stays "assumed").

## 3. Fill model (practice execution)

Inputs: a target exposure at the decision, the candles of the oracle pair, `fee_bps` (default 10 per side), `slip_bps` (default 5), `stress_slip_bps` (default 25), latency `L`.

1. **Fill price.** If trade-level data from the oracle venue is available, the first trade at or after `earliest_fill`; otherwise the **open of the first 1-minute candle that opens at or after `earliest_fill`**, labelled a candle-resolution approximation; the candle that closes at `T0` is never the fill candle. Slippage is applied against the order (higher for a buy, lower for a sell) by `slip_bps`; the stress line instead uses the adverse extreme of that candle (the high for a buy, the low for a sell) plus `stress_slip_bps`.
2. **Missing candle** at the fill: the trade is `UNSETTLEABLE_MISSING` (the forecast event may still settle).
3. **Exits.** Practice stop and target are the contract B offsets applied to the fill price. Evaluated on 1-minute candles starting with the candle **after** the fill: a touch exits at the next executable price after the touch plus slippage; a candle that touches both is treated pessimistically (stop first); the time exit is the first executable price at or after `T0 + horizon`. A gap through a stop realises at the next available price.
4. **Costs** are always shown at several levels (base, stress, double fees).

Why this is stricter than a default engine: NautilusTrader's default fill model applies no slippage (`prob_slippage = 0`) and treats a touched limit order as filled (`prob_fill_on_limit = 1`); its documentation describes models that add one tick of slippage with some probability. Our practice trades are market-style fills with explicit costs, a pessimistic rule for ambiguous candles, and a stress line, and we publish a list of what is still not modelled (market impact, partial fills beyond the candle, queue position, funding, borrow, venue outage).

## 4. Risk kernel (monotone shrink)

`shrink(targets, book, limits) -> (targets', shrink_record)` is a pure function. Properties, each a property-based test:

- **Never increases exposure:** for every token, `|w'| <= |w|` and `sign(w') = sign(w)` or `w' = 0`.
- **Idempotent:** `shrink(shrink(x)) = shrink(x)`.
- **Order-independent as specified:** targets are processed in the order (`|w|` descending, then symbol); the recorded record names every cap that bound (per-token 5 percent, gross 30 percent, net 20 percent, correlation cluster 10 percent) and the factor applied.
- **Limits hold after:** gross ≤ equity, cash ≥ 0, all caps satisfied.
- **A model output can never enlarge a limit:** limits come from the protocol; the model's proposal is an input to the shrink, never to the limits.
- **Drawdown ladder:** at 5 percent drawdown the arm holds half of what the caps would let it hold at no drawdown (the caps are applied first and the result is halved, rounded toward zero, so a deeper rung never lets more through than a lighter one; the shrink is idempotent at every rung but this one, where a second pass halves again), positions flattened at 7.5, positions flattened at 7.5, arm halted at 12 (priors, pre-registered); `HALTED` and `RUIN` are terminal and shown.

## 5. Book accounting

Equity = cash + marks of positions at the 1-minute oracle close; quantities and prices are decimal strings (arithmetic in exact decimals with stated rounding: prices to the oracle tick, quantities to the lot size, money to 8 places half-even); no leverage in the first scored period; synthetic shorts are the negative of the long return over the same entry and exit minus costs and are labelled a synthetic benchmark in their own panel. Every state change is a ledger event, so the book is a fold of the ledger.

## 6. Look-ahead protection

Two tests, both run for every feature and every arm:

1. **Planted future.** A future candle or evidence item (a price of 1,000,000, an item with `known_at` after the decision) is added to the store; the decision and the fill must be byte-identical.
2. **Prefix invariance** (the method of Freqtrade's look-ahead analysis, rebuilt): the value a feature or signal takes at time `t`, computed from data up to `t`, must equal the value computed from data up to `t + Δ`. Any difference is look-ahead. It is applied to the indicator skill (already built), to evidence adapters and to every deterministic transform.

## 7. The independent second implementation

Contract section 7 requires a separately written settlement implementation that reproduces at least 60 hand-built golden cases exactly. Specification of the suite so it can be written by a person who never sees our code:

- Generator categories (each ≥ 3 cases): ties at the oracle tick; ambiguous barrier candles with and without 1-minute refinement; gaps at `T0`, at `T0 + horizon`, and mid-window; holiday-style outages; 1000x aliases; oracle disagreement above and below 0.5 percent; delisting; boundary times for all 60 second-offsets; late token downgrade moving `T0` later, never earlier; three-clock fill cases; short-return synthetic benchmark; costs at three levels.
- Each case is a JSON file: inputs (candles as decimal strings, commitment times, parameters) and the expected output with a one-line reason referencing the contract section.
- The second implementation is written from the contract text by a person other than the first author, in another language, with no shared code; the harness compares outputs; any difference is resolved by amending the contract or one implementation, and the resolution is a recorded finding.
- **Status:** the harness shape exists (E5 compares two implementations of the origin arithmetic); the independent author is an open reviewer role (the engineering standard, section 2b).

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Unfinished bar used as a close | rejected: bar's close time must be before the venue server time read at settlement | stale-bar fixture |
| NaN or `null` in a price | the trade is `UNSETTLEABLE`, never a zero | NaN fixture |
| Same-bar fill | impossible by construction (`earliest_fill >= T0` and the fill candle opens at or after it) | boundary sweep |
| Quote class mismatch between forecast and oracle | rejected | identity chain test |
| Limit that shrink would raise | cannot occur; property test would fail the build | property test |
| Replay of settlement | same outputs (idempotent, keyed by `(forecast_id, oracle_pair, rule_version)`) | replay test |
