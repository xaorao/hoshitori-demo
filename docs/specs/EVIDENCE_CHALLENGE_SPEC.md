# Evidence challenge specification

Scope: counterfactual evidence challenge and perturbation controls. Status: DESIGNED; the statistics were simulated (E8) and the simulation changed two rules. Product rules: contract sections 13, 29, 34.7, 50. Sources studied: Tracekit's `why` (MIT; documentation read in full, code listed: seeded paired replays with tools served from tape, trust-labelled inputs, a content-addressed hash-chained log, labels CAUSAL and RULED-OUT, n = 40 in its demo), Counterfact (Apache-2.0; README and removal-strategy section: ablates or severely degrades whole agent modules and attributes quality by Shapley and leave-one-out), Anchors (BSD-2-Clause; see the sufficiency spec), DoWhy (refutation habit; see the falsification protocol).

## 1. What a challenge is, and is not

A challenge asks: *did removing, replacing or adding this evidence change the decision, beyond ordinary variation?* The answer is **decision sensitivity**. It does not say the model "used" the evidence, how it reasons, that the evidence is true, or what the market will do. The original forecast and receipt stay locked; a challenge is a new linked record.

## 2. Pipeline

| Stage | Computation | Output |
|---|---|---|
| 1 Validate | the receipt exists and is challengeable; the change is structured (a source family to withhold; an evidence item and a replacement value; a claim node and an assumption); the mode is one of three | `ChallengeSpec` (hash) |
| 2 Plan | **dependency-aware intervention** (below); plausibility check; leak check | `Intervention` list |
| 3 Baseline | re-run the deciding arm on the unchanged snapshot `n` times with fixed seed indices `0..n-1` | baseline outcomes |
| 4 Intervene | re-run with the intervention, **the same seed indices, the same stored tool results (tape), the same prompt version** | paired outcomes |
| 5 Controls | unchanged-input control (extra seeds only), placebo control (an irrelevant item of similar size), rules arm on the same change | control outcomes |
| 6 Analyse | paired statistics (section 4) with mechanism attribution | `ChallengeResult` |
| 7 Render | plain-language text: what changed, who changed it, what did not change | page text |

The **rules arm** is evaluated first (deterministic, exact, free); model arms are replayed in an offline batch and cached by `sha256(receipt_id, change, model_version, n, seed_policy)`; a standard set (remove each of the top evidence groups) is precomputed for every featured receipt. Public live challenges use the cheaper tier and are labelled preliminary (section 6).

## 3. Interventions (contract section 29)

| Type | Computation |
|---|---|
| `WITHHOLD_SOURCE` | remove the source's raw items **and every descendant** (lineage graph, data provenance section 4); recompute any derived item that still has other parents |
| `CHANGE_VALUE` | change one raw value; recompute all deterministic descendants with the pinned code; vendor-computed fields (opaque to us) are marked partial unless withheld with their price family |
| `CHANGE_CLAIM` | edit a claim node's assumption; mark every dependant `STALE` (not silently regenerated) |
| `DEGRADE_SOURCE` | keep the item's shape and destroy information: `STALE` (an older snapshot of the same source), `NOISE` (at a pre-registered scale relative to the item's own variation), `COARSEN` (round to a pre-registered grid); always labelled degraded, never shown as live |

**Leak check.** After planning, an automated check walks the lineage graph and confirms that no item in the remaining context has a removed or changed item among its ancestors; on failure the run is `NOT_EVALUATED(leak)`. **Plausibility.** A replacement must be a value that occurred in a recorded bundle of the same regime or lie within the item's pre-registered range; a combination that never occurred is excluded or labelled hypothetical.

## 4. Statistics, as set by E8

Per condition there are `n` paired runs of baseline `x_i` and intervened `y_i` (the action class equals the original, or not). From the 2×2 counts of pairs:

- **Interval for the shift:** Newcombe's paired method (Wilson limits for each proportion corrected by the pair correlation), because the paired Wald interval undercovers at small n (E8, nominal 0.95, over within-pair correlations 0, 0.5 and 0.8 and four effect sizes: Wald minimum coverage 0.71 at n = 5, 0.87 at n = 10, 0.92 at n = 20, 0.93 at n = 40; Newcombe minimum 0.88, 0.92, 0.93, 0.94). Some cells over-cover (up to 0.99 at n = 5), which is the conservative direction. See `E8_results.json`.
- **Test of "any change":** exact McNemar (binomial test on the discordant pairs). Measured type I error was at most 3 percent at every n tried. Measured power (within-pair correlation 0.5): at n = 20, 20 percent for a 0.2 shift, 76 percent for 0.4, 99 percent for 0.6; at n = 40, 54, 98 and 100 percent; at n = 10, 1, 18, 62 percent; **at n = 5 it is zero for every shift**, because the smallest attainable p is 0.0625.
- **Probability shift:** for arms that issue probabilities, the paired mean difference of `p` with a paired bootstrap interval; reported beside the action result, never instead of it.
- **Labels** (replacing the first draft's fixed thresholds, contract findings CF-15 and CF-16):
  - `DECISION_CHANGED`: n ≥ 20, exact McNemar p ≤ 0.05, and the Newcombe interval excludes the control's interval (the control shift's own interval must include zero; if the unchanged-input control itself shows a change, the model is too unstable to read and the result is `NOT_EVALUATED(unstable_baseline)`).
  - `DECISION_STABLE`: the Newcombe interval of the shift lies within ±0.15 **and** n ≥ 60 (n ≥ 40 when the observed within-pair concordance is at least 0.8): at n = 20 the median half-width of the interval for a null shift is 0.16 to 0.22 and only 3 percent of null trials fit inside ±0.15 (26 percent at concordance 0.8); at n = 60, 99 percent do (E8 half-width table).
  - `EFFECT_UNCERTAIN`: anything else, including every preliminary 5-run result and every 20-run result that is not clearly changed. A source is never called irrelevant from a handful of stochastic runs; there is no `RULED-OUT` label.
  - `NOT_EVALUATED(reason)`: leak, unstable baseline, insufficient variation, unreproducible run.
  - `EVIDENCE_REQUIRED`: the removed source is on the arm's required list; shown as a result, not a failure.

Cost implication: at about two model runs per pair, a 60-pair challenge costs 120 runs plus controls; the budget gate (section 6) and the precomputed standard set make that affordable for featured receipts and offline for others.

## 5. Mechanism attribution

Every replay stores the model's own proposal and the final action after the deterministic gates (grounding, required evidence, minimum edge, risk kernel, falsification). `change_mechanism`: `MODEL_REVISED` (the proposal changed beyond the control while the gates did not change the outcome), `GATE_BLOCKED(gate)` (the proposal stayed within the control but a named gate changed the final action; `EVIDENCE_REQUIRED` is the required-evidence gate), `BOTH`, `VARIATION_ONLY`, `NEW_INFORMATION` (mode `NEW_EVIDENCE_UPDATE`). Precision and the model-change statistic are computed on proposals; the final-action line is separate. Only `MODEL_REVISED` is shown as "the agent changed its mind"; `GATE_BLOCKED` is shown as "a safety rule stopped it" and counts as failure-handling evidence, never as reasoning.

## 6. Modes, budgets and safe input

- **Modes** (never mixed): `AS_OF_AUDIT` (only information that provably existed at the original `as_of`, shown with its proof), `NEW_EVIDENCE_UPDATE` (information after `as_of` or of unprovable earlier existence; never a defect in the original decision), `HYPOTHETICAL` (invented values, labelled on every surface). Each record stores its mode.
- **Budgets.** Per visitor 5 live challenges per hour and 20 per day (priors); a global daily model-credit budget; at exhaustion cached results only, said so on the page. Challenges needing a fresh full run are queued to the offline batch. All of this sits behind the same gateway budget classes as other model work.
- **Safe input.** User-supplied evidence is accepted only as structured fields (claim, source link, quoted span), wrapped as untrusted text, never given tool access or the power to change instructions; a link is fetched by the safe client, not by the model; the injection suite runs in CI.
- **Reproducibility.** Seeds, prompts, model ids and recorded evidence are stored so a result can be re-displayed without re-running.

## 7. How this compares with the sources

| Source | Does | We add |
|---|---|---|
| Tracekit `why` | seeded paired replays with an interval; tools from tape; trust labels; CAUSAL and RULED-OUT labels; demo n = 40 | descendant removal through declared lineage with a leak check; three modes with as-of proof; degradation as well as omission; unchanged-input and placebo controls; no RULED-OUT from a few stochastic runs; labels derived from measured interval widths; mechanism attribution that separates the model from the gates |
| Counterfact | ablates or degrades agent modules and attributes quality | its target is the agent's modules; ours is the evidence items and their families |

Both are used under their licences as design references; code is adopted only where cheaper than writing it (decision at build time, with credit).

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| A descendant of a removed source remains in context | `NOT_EVALUATED(leak)` | planted descendant |
| The unchanged-input control shows a "change" | `NOT_EVALUATED(unstable_baseline)` | high-temperature fixture |
| Placebo removal labelled `DECISION_CHANGED` more often than the control's false-positive rate | build fails the benchmark | the placebo benchmark |
| n = 5 result | at most `EFFECT_UNCERTAIN`; discordant counts and interval shown | label table test |
| A gate changed the action | `GATE_BLOCKED`, not "the agent changed its mind" | gate fixture |
| A hypothetical value presented as live | rejected by the mode label lint | copy lint |
| Same question asked twice | cached result, no recomputation | cache test |
| Budget exhausted | cached results only, stated | budget test |

Connection to the journey: this is the "one evidence challenge" step. The visitor removes or corrects one item and sees, with honest uncertainty, whether the decision moved and who moved it.


## Rules from an earlier study (reproduced where marked)

1. The paired interval is Tango's score interval or an exact interval; the Newcombe form as implemented gives a zero-width interval when no pair is discordant (reproduced), which would pass the stability test with a handful of runs.
2. The disjoint-from-control requirement is dropped from the decision rule: it costs most of the stated power and is not needed for validity because the paired test already measures the shift against ordinary variation. The power quoted for 20 runs is the exact test's, not the label's.
3. The precomputed standard set is one family of tests: Holm correction over it (reproduced: 15 to 16 percent false flags without it at 60 runs over eight null groups); a group-first step withholds all top groups together and tests each group left in alone.
4. The leak check also runs at every model call during a replay, because a replayed agent can call a tool again and receive the withheld family back; in as-of mode a call the original never made is served from the stored snapshot or refused.
5. A run that errors, times out or returns an unparseable reply is `RUN_FAILED(reason)`, counted in the denominator; above a threshold the result is `NOT_EVALUATED(structural_failure)`.
6. The estimand is the full paired table over all actions (Stuart-Maxwell), not only "same as the original"; the binary test is the headline only for two actions.
7. Seed pairing is measured for the chosen model before it is relied on; power is quoted at the measured correlation or at zero; a group-sequential plan (looks at 20, 40, 60) is evaluated in a re-run of E8 before replacing the fixed n.
