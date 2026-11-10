# Evaluation protocol

Scope: forecast evaluation, statistical validity, fair comparison. Status: DESIGNED; the interval procedure was tested on synthetic data (E6, E6b) and **the procedure the contract planned fails its own coverage test**; the replacement and the rule that forces such a replacement are below. Normative product rules: contract sections 9, 10, 27 (populations), 32 (arenas), 7 (exclusions and bounds). Sources studied: ForecastBench (code and documentation read: the difficulty-adjusted Brier is a two-way fixed-effects model, `brier_ij = a_i + b_j + u_ij`, with `b_j` the question difficulty), Metaculus scoring FAQ (read: log score underlies baseline and peer scores; predictions clipped to 0.1 percent to 99.9 percent; scores are time-averaged and coverage-weighted), scikit-learn calibration documentation (read: isotonic outperforms sigmoid only above about 1,000 samples and overfits small sets), Bailey, Borwein, López de Prado and Zhu on backtest overfitting (full text extracted and read through the CSCV procedure), Inspect AI (coverage reporting issues noted in the landscape study).

## 1. Units, populations and denominators

- **Inferential unit: the market day** (UTC). Calls on one day share a regime, so calls are not independent (E6 shows what happens if they are treated so, section 4).
- **Population chain** (contract section 27): P0 scheduled candidate × arm × horizon; P1 attempted; P2 completed before cutoff; P3 independently committed (C3 or above); P4 resolvable; P5 matched (all compared arms present on the same candidate and horizon). Primary comparisons use P5 with the same `T0_cycle`. Every results page shows the whole chain and the attrition between steps by reason, arm, source failure and volatility regime.
- **Never select on success.** Headline figures use the pre-registered population; a second line shows all committed levels and the share that missed C3; a test checks whether dropping out of P3 or P4 depends on volatility regime or asset, and if it does the headline carries a missingness warning with the best-case and worst-case bounds of contract section 7.
- **Every arm, including failed and late ones, appears** in the availability and latency table; `FAILED`, `FAILED_EVIDENCE`, `FAILED_LATE` and voluntary `NO_CALL` are separate columns, never merged.

## 2. Scores

| Quantity | Definition |
|---|---|
| Brier (binary) | `(p - y)^2`, `p` clipped to [0.01, 0.99] (the contract's prior; Metaculus clips to [0.001, 0.999]) |
| Log score | `y ln p + (1 - y) ln(1 - p)` on the clipped `p`; always negative; reported alongside Brier because it punishes extreme errors |
| Skill | `1 - mean score / mean reference score` for the Brier and for the log score, against two references fixed in advance: constant 0.5 and the climatological rate of the preceding 30 days (outcomes that became known in that window and before the forecast was created, at least 20 of them, a prior; never later data); a forecast without a reference is left out of that comparison on both sides |
| Barrier (3-class) | multi-class Brier over `TARGET`, `STOP`, `TIMEOUT`; `AMBIGUOUS` and `UNSETTLEABLE` excluded and reported as a rate |
| Coverage, selective risk | coverage = calls / (calls + voluntary no-calls), availability = 1 - failures / everything eligible; selective risk = mean Brier of the settled calls; abstention is not scored |
| Calibration | reliability diagram on equal-count bins with the count per bin; a calibration map is used only above about 1,000 settled calls (isotonic) or, below that, a two-parameter sigmoid or none; the map is versioned, trained only on data before the period it is applied to, and stored on each forecast (contract section 20) |
| Cross-cycle comparison of forecasters who faced different candidates (arenas) | the two-way fixed-effects form used by ForecastBench: `score_ij = a_i + b_j + u_ij`, `a_i` the forecaster's difficulty-adjusted score |

Metaculus' coverage-weighted time-averaging is not needed in the league (all arms commit once, in one batch, at one origin); it is relevant only if a participant may update a forecast, which the arena rules forbid.

## 3. Comparisons

- **Paired.** For arms A and B and a metric, the statistic is the mean over market days of the per-day mean difference on days where both had eligible calls. Overlap of individual intervals is not used.
- **Primary list and multiplicity.** The protocol names a short list of primary comparisons (default: each arm against the rules baseline on 24-hour directional Brier skill; L2 against the matched-budget single-call control L1x) and controls the family-wise error across it with Holm's step-down procedure. Everything else is exploratory, labelled so, with unadjusted intervals; no ranking claim rests on it.
- **Smallest effect and power.** The protocol fixes the smallest effect of interest per primary comparison; power (target 0.8) is established by simulation using the variance and autocorrelation observed on the exploratory period, not assumed; otherwise the table says "insufficient power" and shows no rank.
- **Minimum sample for any ranking claim:** the contract's thresholds (20 settled calls and 10 independent market days for "provisional"; more for ranked) stand, and this protocol adds the coverage rule of section 4.
- **Concentration test and redundancy:** unchanged (contract section 10).

## 4. Interval procedure, tested before it is used (E6, E6b)

**Rule (new, from the findings):** an interval procedure may appear on a results page only if its empirical coverage on synthetic data matching the observed autocorrelation of the real series is within 0.03 of nominal; the achieved coverage is published beside the interval; a procedure that fails is replaced before the scored period and the replacement is a protocol version.

**What was run.** Synthetic paired score differences with calls clustered in days (Poisson mean 4.5 per day), a day effect with first-order autocorrelation φ, a known true effect, 800 or 600 replicates per cell, 90 percent nominal. Procedures: bootstrap over calls (naive), bootstrap over days, moving-block bootstrap over days (blocks of 5, 10, 20), t interval on day means, Newey-West (Bartlett) t interval on day means.

| Finding | Evidence (coverage of a nominal 0.90) |
|---|---|
| Treating calls as independent is wrong whenever days have an effect | call-level bootstrap covered 0.60 to 0.79 (false-positive rate of "excludes zero" 21 to 40 percent) for day effect sd 0.5 at 20, 50 and 100 days; it covers correctly (0.89) only when there is no day effect |
| With independent days, the t interval on day means is right and the percentile block bootstrap is too narrow | 50 days: t 0.90 to 0.94, block-5 0.84 to 0.85, block-10 0.81, block-20 0.75 |
| With autocorrelated days (φ = 0.5) nothing standard reaches nominal | 50 days: t 0.78, Newey-West 0.83, block-5 0.82; 100 days: t 0.79, Newey-West 0.87, block-5 0.85; 200 days: t 0.78, Newey-West 0.87, block-5 0.85 |
| With strong autocorrelation (φ = 0.8) all fail badly | Newey-West 0.71 to 0.74, blocks 0.69 to 0.78, t 0.54 to 0.59, at every size up to 200 days |
| Longer blocks do not fix it at these sizes | block-20 at 200 days: 0.86 (φ = 0.5), 0.78 (φ = 0.8) |

**Consequences.**
1. The contract's planned procedure (block bootstrap by market day, with blocks of 5 days) **fails the coverage rule** in the autocorrelated settings (0.82 to 0.85 where 0.90 is nominal) and is replaced as the *primary* interval by the **Newey-West t interval on the daily mean paired differences** (bandwidth `floor(4 (D/100)^(2/9)) + 1`), whose coverage was highest of the tested procedures when autocorrelation is moderate (0.83 to 0.87), with the block bootstrap kept as a secondary check. Where the estimated first-order autocorrelation of the daily differences exceeds 0.5, the page says "interval is approximate (autocorrelation)" and the nominal level is raised to the level that achieves the target coverage in the matching simulation, or no interval is shown and the comparison reads "not distinguishable".
2. The simulation that produced these numbers is part of the protocol: it is re-run with the observed autocorrelation and variance before every scored period, and its output (coverage by method) is published.
3. The experiment models the day effect in the paired difference; the real series' autocorrelation is unknown until the exploratory period, which is the design reason for the exploratory period.
4. Limits of this evidence: Gaussian synthetic data; Poisson call counts; two variance settings; first-order autocorrelation only; coverage at 90 percent only. It shows the planned procedure can fail, not that the replacement is sufficient.

## 4a. Reporting a comparison

Every comparison shows: the estimate, the interval with its method and its **empirical coverage from the matching simulation**, days and calls, effective sample size (days adjusted for autocorrelation: `D / (1 + 2 Σ ρ_l)` with the Newey-West weights), regimes covered, and the population (P5) with its attrition. A comparison that is not a pre-registered primary one carries the word "exploratory".

## 5. Selection among variants (models, prompts, sources)

Choosing the best of many variants on the same data inflates apparent skill (Bailey et al.: a 5 percent false-positive rate holds only when a test is applied once; the cross-validation based estimate of the probability of backtest overfitting (CSCV) needs only the matrix of performance series across variants). Rules: all variant selection happens on the exploratory period and a separate validation set; the number of variants tried is recorded and published; for any selection among ten or more variants, the CSCV estimate of the probability of overfitting is computed on the exploratory matrix and reported; the scored period uses exactly one pre-registered variant per arm. (CSCV is a diagnostic here; it is not implemented as an experiment yet.)

## 6. Pre-registration content

Candidate and event rules (versions and hashes), schedule, horizons, contracts and parameters, oracle list, cost levels, baselines, arm definitions and model ids, minimum thresholds, the primary comparison list with smallest effects and powers, the interval procedure and the simulation settings, exclusion rules and sensitivity bounds. The protocol hash is anchored with independent tokens before a scored period; a change is a new protocol and a new scored period.

## 7. Failure modes and tests

| Failure | Required behaviour | Test |
|---|---|---|
| Interval method untested for the observed autocorrelation | not shown | rule check in the results builder |
| Missing cells | shown as missing, never zero | pairing test |
| An arm absent at a horizon | absent from that horizon's tables | eligibility test |
| Results page computed outside the shared eligibility service | impossible | contract section 10 test |
| Selection on success (only C3, only settled winners) | headline uses the pre-registered population; sensitivity bounds shown | attrition fixture |
| Variant count not recorded | protocol invalid | audit |

Connection to the journey: the honest-outcome step of the journey is this document made visible: a result shows its population, its attrition, its interval's method and its achieved coverage, so a viewer can see why it is called provisional.


## Open experiment from an earlier study (reproduced)

The textbook Newey-West interval chosen in section 4 covers 0.78 to 0.86 against a nominal 0.90 whenever the day effect has autocorrelation 0.5 or more and at 20 days, and 0.75 to 0.83 for overlapping 7-day windows, so by the coverage rule it would be labelled approximate in most cells. The equal-weighted cosine estimator with a fixed-b quantile stayed within 0.03 up to autocorrelation 0.5 (0.882 to 0.906) and for overlapping windows from 50 days (0.87 to 0.90). The contract's estimator changes only after E6b is re-run with it and the real day counts.
