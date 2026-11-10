# League experiment specification

Scope: multi-agent debate, controlled league, RYO reference arm. Status: DESIGNED. Product rules: contract sections 6, 10, 25, 27, 43. Sources studied: TradingAgents (roles, risk team, memory; no ablation of debate against a single call was found in its code or tests, paper not checked), ai-hedge-fund v2 (ledger, scorecard; MIT), ForecastBench (matched questions, baseline forecasters, difficulty-adjusted comparison), Inspect AI (repeatable tasks and logs), the competitors' released behaviour (Nota's council, Wagyu's triage; M-04).

## 1. What the experiment is for

To answer, with a fair comparison and an honest denominator, three questions: (1) does a model add anything over deterministic rules on identical evidence; (2) does a debate add anything over the same compute spent on a single judgment; (3) how does RYO's own interpretation compare on the measures it can be scored on. A positive answer to none of them is a legitimate, publishable result. The experiment is the controlled regime only (contract 46.1); it never claims live tool use.

## 2. Arms and their information classes

| Arm | What it is | Receives | Produces | Probability? | Compared with |
|---|---|---|---|---|---|
| `L0` rules | deterministic rules on observations, with a frozen mapping from rule firing to a historical base rate (`RULES_ARM_SPEC.md`) | observations only (never RYO interpretation) | action, `p` via the frozen map | yes (mapped) | all |
| `L1` single call | one typed model judgment | observations + independent sources | action, `p`, invalidation, cited evidence | yes | all |
| `L1x` matched compute | the L1 method run several times, `p` aggregated by median | same as L1 | same | yes | L2 only (primary pair) |
| `L2` debate | Bull, Bear, Judge roles | same as L1 | same + disagreement map | yes | all |
| `R` RYO | RYO's own recorded `deep_analysis` interpretation (verdict, plan) | nothing from us | direction, plan levels | **no** | panel 1 only |

The observation versus interpretation table of RYO fields is generated from the live catalogue and committed before the first scored period (contract section 6); the arms L0 to L2 never receive an interpretation field. A run in which an arm sees one is void and counted.

## 3. Comparison matrix (never merge panels)

| Panel | Includes | Measures |
|---|---|---|
| 1 Decision outcomes | `L0, L1, L1x, L2, R` | directional hit rate against the base rate; barrier outcomes with each arm's own levels (descriptive); the practice portfolio at identical rules; coverage and availability |
| 2 Probability skill | `L0` (mapped), `L1, L1x, L2` | Brier, log score, skill against references, calibration; selective risk |
| Not comparable | `R` on panel 2 | none: RYO issues no probability for our events; it is never given a score |

## 4. One cycle, as run

The sequence of `DISCOVERY_MANIFEST_SPEC.md` ends in `RUNNING`. For each arm, each candidate and each horizon the arm reads the frozen snapshot only. The harness records, per arm: start and end time, `ModelCall`s (tokens, latency), outputs, the final record after gates. Arms run concurrently with a per-arm cutoff (contract section 25 item 7); an arm that misses the cutoff is `FAILED_LATE` and may not update. All accepted outputs are committed in one batch.

## 5. Matched compute (the L1x control)

Debate may win only because it spends more. So L2's spend is capped by a pre-registered budget `B` (total billed tokens per candidate and horizon), and L1x is configured to spend the same. **One matching rule:** total billed tokens, defined as input tokens (cached input counted in full, at its uncached size) plus output tokens (the completion count of the provider's usage record, which on the endpoint we use already contains the reasoning tokens, so those are not added again, CF-62), taken from the usage record of each `ModelCall`; the number of calls is reported but not matched. **How `m` is set:** per candidate and horizon, after L2 has run, `m = max(3, round(tokens_L2 / mean_tokens_per_L1x_sample))`, made odd by rounding up, so the matched pair uses the same snapshot and a size derived from that pair rather than a global mean (a global mean made mismatches frequent). **No post-treatment exclusion:** a pair whose realised tokens still differ by more than 10 percent (prior) is kept in the primary comparison and flagged `MISMATCH`; the primary result is reported with and without the flagged pairs and stratified by the sign and size of the mismatch, because dropping pairs by how much L2 happened to spend selects on the treatment. **Aggregation:** the median `p` of the `m` samples; the action is derived from the median `p` by the mapping L1 uses (no separate majority; an odd `m` removes the median tie). **Independence:** the samples are independent requests with distinct request ids; L1x uses the same model, prompt family and temperature as L1, and if the temperature is 0 or the provider ignores sampling, byte-identical samples are flagged `COLLAPSED_L1X` and counted (a collapsed L1x is not a valid control and the pair is reported as such). **Abstention:** L2's `no_consensus` is a `NO_CALL`; the primary paired comparison is on pairs where both arms made a call, and a pre-registered sensitivity analysis scores every `NO_CALL` as the fixed climatological forecast so that abstaining cannot improve a Brier score by selection. The credited improvement of L2 is its paired difference to **L1x**, never to L1.

## 6. Roles inside L2 (so a debate is more than three copies of one prompt)

Bull and Bear each return a typed object: a falsifiable claim, supporting and opposing evidence ids, an explicit uncertainty, and the single observation that would change their view. The Judge returns the arm's decision or `no_consensus`. Disagreement is preserved for the disagreement map. Roles use distinct prompt versions and may use distinct model temperatures; two roles whose outputs are byte-identical are flagged `COLLAPSED_DEBATE` and counted. TradingAgents' structure (analysts, bull and bear researchers, risk debaters, a manager) is the reference for the role set; its token-cost and prompt-growth issues (read in the landscape study) are why `B` is a hard cap.

## 7. Latency, horizon eligibility and attrition

Per arm and horizon the gap bound of contract section 25 limits eligibility: an arm whose 95th-percentile latency exceeds the horizon's cutoff is "not eligible at this horizon" and is absent from that horizon's tables, never scored as a failure there. The harness reports: latency distribution, `FAILED_LATE` rate, availability, the number of `NO_CALL` by reason, attrition by population step (evaluation protocol, section 1).

## 8. The reasoning-value report

Per candidate and horizon the harness stores the action of `L0`, `L1`, `L1x`, `L2`. The report lists `AGREE` cases, `DEBATE_CHANGED` cases (L2 differs from L1x: both outputs, evidence cited by one and not the other, outcome when settled, extra tokens and seconds), and the paired L2-versus-L1x comparison on the pre-registered endpoint. A change without a defensible reason is unexplained variation, not insight. The report states "debate did not help" when that is what the comparison shows (contract section 10).

## 9. Ablations (removal test)

On experiment cycles every evidence source family is withheld in turn on a held-out period (RYO as a family, the exchange series, news, voices once validated); the decision change and the outcome difference are reported per route. These are decision-sensitivity results (contract 13) plus matched-run outcome differences, never causal claims about the market.

## 10. Cost model

Per cycle: RYO calls (gateway design), exchange weight, model tokens per arm (from `ModelCall` records), and the TSA requests. The architecture computes the per-cycle token cost from `B` and the number of candidates; a daily budget gate refuses to start a cycle that would exceed it and records `CYCLE_SKIPPED(budget)`, so spend never silently truncates an arm.

## 11. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Arm sees an interpretation field | run void, counted | leak test with a planted field |
| L1x tokens differ from L2 by more than 10 percent | pair flagged `MISMATCH`, kept in the primary comparison, reported with and without and stratified by mismatch | accounting test |
| L1x samples byte-identical (temperature 0 or a provider that ignores sampling) | flagged `COLLAPSED_L1X`; the pair is not a valid control and is reported | collapse test |
| Two debate roles identical | `COLLAPSED_DEBATE` counted | fixture |
| One slow arm | late arm `FAILED_LATE`, others share `T0_cycle` | cutoff test |
| R compared on panel 2 | impossible: the table builder has no `p` for R | schema test |
| Pair absent at a horizon | absent from that horizon's tables | eligibility test |

Connection to the journey: the league is not on the visitor's first screen; it supports the Track 1 claim (a fair comparison with a visible denominator) and feeds the Track Record screen.
