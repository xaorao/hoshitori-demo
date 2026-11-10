# Falsification protocol

Scope: falsification and competing market explanations. Status: TESTED for three of the six tests, which are built in `falsify` and calibrated on planted synthetic scenarios (`falsify_calibration`; experiment E7), where one rule had to be replaced because it failed; the others are DESIGNED. Product rules: contract section 48 (the step), 34.3 (a market causal claim is never marked supported), 55 (outcome review). Sources studied: DoWhy (MIT; README and refuter tree read: model, identify, estimate, refute with placebo-treatment, random-common-cause and data-subset refuters), the contract's own onset and market tests, Qlib and AI-Trader (listed, not mined for this), the AI-Trader finding that risk control decides robustness (abstract). The causal-inference literature was **not** read in full here (NOT STUDIED: the Brady Neal and Hernán–Robins texts named in the source map); nothing below claims causal identification.

## 1. Three things that must stay separate

| Kind | Example statement | What can support it |
|---|---|---|
| Association | "the move followed the announcement" | the timing and magnitude tests below |
| Model sensitivity | "the agent's decision changes if this evidence is removed" | the Evidence Challenge Engine |
| Causal market effect | "the announcement caused the rise" | not established by any test here; the output may say "associated with" and must list the competing explanations that survived |

A text that says *caused* without a list of surviving alternatives fails the language lint. DoWhy's discipline is the borrowed idea: state the assumptions, then try to refute them; the refuters are placebo treatment (replace the treatment with random values), random common cause, data subset, and unobserved common cause. We implement the first and third in market form and use the structure (explicit assumption, registered refutation, labelled result); we do not claim a causal model.

## 2. The record

`FalsificationRecord` (contract 48.2): `target_assumption` (a claim-graph node), `proposed_explanation`, `competing_explanations[]` each with what it would predict differently, `tests[]` and `unresolved[]`. Each test result is `FAVOURS_ORIGINAL`, `FAVOURS_ALTERNATIVE`, `INCONCLUSIVE` or `NOT_TESTABLE(reason)`; a test that needs data we do not have is `NOT_TESTABLE`, never skipped. Consequences: an alternative-favouring result can only reduce exposure through the risk kernel (monotone shrink); the step is mandatory before every committed forecast and every thesis page that reaches `COMPLETED_AUDIT`.

## 3. The test library (as implemented in E7 unless stated)

All tests are deterministic functions of stored price and evidence series with registered parameters; none calls a model. Notation: `s` is a robust local volatility (median absolute deviation of the preceding 6 hours of 1-minute returns, scaled by 1.4826), `TA` the announcement minute, `H` the forward horizon.

| Test | Computation | Result rule |
|---|---|---|
| `T_MARKET` | beta of the token on a market proxy over a history window before the event; residual return over `[TA, TA + H]`; the proxy and peer basket are pre-registered | `FAVOURS_ORIGINAL` if the residual is at least `k` local residual standard errors (k = 3); `FAVOURS_ALTERNATIVE` if the residual is small but the raw move is large (the market explains it); else `INCONCLUSIVE` |
| `T_ONSET` | **window** `[TA − 60, TA + H]`: `z_total`, `z_pre` over `[TA − 60, TA − 5]`, `z_post` over `[TA, TA + H]`, each the sum of 1-minute returns over `s sqrt(minutes)` | `INCONCLUSIVE` if `abs(z_total) < 3` (no move to date); `FAVOURS_ALTERNATIVE` if `abs(z_pre) >= 2.5` with the sign of `z_total` (the move began before); `FAVOURS_ORIGINAL` if `abs(z_post) >= 2.5` with that sign; else `INCONCLUSIVE` |
| `T_PLACEBO_TIME` | the event statistic `abs(sum over [TA, TA + H])` against 200 draws of the same statistic at placebo times in the preceding history (excluding the neighbourhood of known events); empirical two-sided p = (1 + count of placebo values at least as large) / (201) | `FAVOURS_ORIGINAL` if p ≤ 0.05; `FAVOURS_ALTERNATIVE` if p > 0.2 (the move is not unusual for this token); else `INCONCLUSIVE`. It measures unusualness, not attribution |
| `T_SOURCE` | the cited source supports the interpretation (grounding contract) | per the support layers |
| `T_SUBSET` (designed, not run) | the association's sign agrees across venues and across sub-windows (at least 80 percent agreement) | labelled exploratory |
| `T_BASE_RATE` (designed, not run) | how often events of this class preceded a similar move in the exploratory history | labelled exploratory; reports the count |

## 4. What E7 showed (planted synthetic scenarios, fat-tailed returns, 800 trials per scenario)

In `../experiments/E7/E7_results.json` the first onset rule is `T_ONSET` and the current rule is `T_ONSET2`. Scenarios: A a token-specific jump after the announcement; B the same drift starting 40 minutes earlier; C a market-wide move that the token follows with beta 1.2; D no effect.

| Test | A true effect | B early onset | C market-wide | D nothing |
|---|---|---|---|---|
| `T_MARKET` (favours original / favours alternative / inconclusive) | 0.99 / 0.00 / 0.01 | 0.07 / 0.03 / 0.90 | 0.06 / **0.70** / 0.25 | 0.05 / 0.04 / 0.91 |
| `T_PLACEBO_TIME` | **0.89** / 0.03 / 0.08 | 0.05 / **0.80** / 0.15 | 0.87 / 0.04 / 0.09 | 0.05 / **0.81** / 0.14 |
| `T_ONSET` as first written (running maximum over a long window) | 0.36 / **0.25** / 0.40 | 0.12 / 0.50 / 0.38 | 0.34 / 0.27 / 0.39 | 0.02 / **0.27** / 0.71 |
| `T_ONSET` as now specified (one look per segment) | 0.40 / **0.05** / 0.55 | 0.00 / **0.45** / 0.54 | 0.42 / 0.04 / 0.54 | 0.01 / 0.02 / 0.97 |

Findings, each with its limits:
1. **The first onset rule failed its own calibration.** The running-maximum rule flagged "began earlier" in 25 percent of true-effect trials and 27 percent of null trials: a multiple-look inflation. It would have told users that genuine announcement effects were pre-existing movement. It is replaced by the one-look rule, whose false-alternative rate is about 5 percent in the true-effect scenario and 2 percent under the null, and which detects the planted early move in 45 percent of trials (power is modest because the planted total move is about four hourly standard deviations). This is the case for calibrating every falsification test on planted data before it is allowed to change a stance.
2. `T_MARKET` works as intended on these scenarios: it does not blame the market for a token-specific move (0.00 false alternative) and flags the market-wide move 70 percent of the time. It stays silent (inconclusive) when nothing happened.
3. `T_PLACEBO_TIME` separates unusual from usual moves (A versus B and D) and cannot separate a token-specific move from a market-wide one (C scores like A), as designed; it is a base-rate check, not an attribution.
4. **Limits of this evidence:** synthetic Student-t returns with volatility regimes; a single effect size; one proxy; no jumps in the proxy history; no intraday seasonality; all thresholds are priors. The tests are a calibrated *starting* library, not validated on real events. Real-data validation needs a labelled set of real events.

## 5. Competing explanations register

The register lists the explanation classes the tests can speak to: market-wide move, move already under way, ordinary volatility for this asset, source does not support the interpretation, venue-specific artefact, and "unknown". The agent must name at least the first four for any announcement-driven thesis and report each as surviving, favoured, or not testable. New classes enter the register only with a test and a planted-case calibration.

## 6. Interface and language

The thesis page block *What we tried to disprove* shows the chain: observation, proposed explanation, competing explanation, test, what remains unresolved. Layer 1 may use its outcome in the *what is still uncertain* slot. Language lint: banned without a surviving-alternatives list: *caused*, *because of*, *driven by*, *due to*; allowed: *associated with*, *followed*, *preceded*.

## 7. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Move began 40 minutes before | `T_ONSET: FAVOURS_ALTERNATIVE` (power-limited; reported) | E7 scenario B |
| Peers moved equally | `T_MARKET: FAVOURS_ALTERNATIVE` | E7 scenario C |
| Not unusual against placebo times | `T_PLACEBO_TIME: FAVOURS_ALTERNATIVE` | E7 scenarios B, D |
| Missing proxy or history | `NOT_TESTABLE` with the reason | fixture |
| A test with a false-alternative rate above 10 percent on planted true effects | cannot ship | calibration gate |
| Thesis with an alternative-favouring result | exposure cannot exceed the pre-test target | risk kernel property test |
| Causal wording without alternatives | build lint fails | lint test |

Connection to the journey: the "one investigation" ends with the agent having tried to prove itself wrong; the visitor sees that attempt and its result before the commitment.


## Rules from an earlier study (reproduced)

1. The calibration gate bounds the decision-level union, not each test alone: with three tests the union of false alternatives on planted true effects was 0.076 (reproduced); it also bounds false reassurance (the step said "survived" in 19 percent of trials on planted market-wide moves). The gate is re-run whenever a test is added.
2. `FAVOURS_ALTERNATIVE` at p above 0.2 is a non-rejection and is relabelled `NOT_UNUSUAL` (or shows its power).
3. Placebo windows are matched on local volatility, because announcements raise volatility; calibration includes scenarios with event-linked volatility.
4. The market-beta window is the pre-event window used by E7; the contract and this protocol say the same.
5. DoWhy's default placebo for a float treatment is a constant column; only a permutation is meaningful, and `T_PLACEBO_TIME` is a different test (a randomisation test of the observed statistic).
