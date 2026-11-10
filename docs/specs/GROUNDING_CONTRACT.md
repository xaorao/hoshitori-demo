# Grounding contract

Scope: claim-to-evidence semantic support and numerical grounding. Status: DESIGNED. Product rules: contract sections 18, 47, 53. Sources studied with the code read: FActScore (`factscorer.py`), TruLens (`groundedness_measure_with_cot_reasons` in `llm_provider.py`), FEVER (label scheme), ARES (judge validation), Vibe-Trading's grounding package (read in the landscape study: the model declares each figure's kind and code verifies it against the run's tool results), FinRobot (compute-versus-narrate separation, README principle).

## 1. The problem in one example

An agent cites a real exchange response that contains a funding rate, in support of "funding rose sharply"; the cited value is negative and unchanged. The citation exists; it does not support the sentence. A grounding layer that only checks existence passes it. This contract defines what is checked, in what order, who has a veto, and what the output is.

## 2. What the studied systems do, and where they fail us

| System | Does | Fails for our purpose (read in code) |
|---|---|---|
| FActScore | splits a generation into atomic facts, retrieves five passages per fact, asks a model "True or False?", reports the share supported | the answer is parsed by substring heuristics (`true`, `false`, a keyword fallback on negations); **its reply parser counts an empty reply, an error message and "False. The claim is not true." as supported** (reproduced in the a later study; the no-model mode overrides the label with a probability and is not the reachable default); two labels only (no "not enough information", no scope); no deterministic number, unit, sign or period check |
| TruLens groundedness | splits into sentences, drops trivial ones, has a model grade each 0 to 3 with reasons, averages | an average lets one fabricated number hide among grounded sentences; **abstentions count as grounded** (docstring); the model's reply to the trivial-statement filter is run through Python `eval()`, so it is never a dependency on our path; the grading criterion tells the judge to accept indirect or implicit evidence; a statement that cannot be parsed drops out of the average; no deterministic veto; no scope labels |
| FEVER | three labels (supports, refutes, not enough info) with annotated evidence | a dataset and a scheme, not a runtime; no numeric semantics |
| ARES | validates a cheap judge against a small human-labelled set; prediction-powered inference gives an interval for a population rate (the share of a system's answers that are good) | evaluates a judge, does not check numbers; its judge accuracy is a plain number with no interval, so the interval method is not transferable to per-stratum recall (a later study, reproduced) |
| Vibe-Trading grounding | the model declares each figure with a kind; code verifies against the run's tool results; rejected claims become corrections | built for equities and its own tool set; its typed-figures idea is what we adopt |

Decision: ADAPT the typed-declaration idea (Vibe-Trading, MIT), the label scheme (FEVER), the atomic unit (FActScore), and the judge-validation method (ARES); reject the defaults named above.

## 3. Typed assertions, so numbers are never parsed from prose

A model output that makes a numerical, comparative or temporal statement does it in a **typed assertion**, from which the prose is rendered by template:

```
Assertion {
  assertion_id, kind: NUMERIC | COMPARISON | DIRECTION | EVENT | MECHANISM | OPINION,
  asset: asset_id,
  quantity: <field path in the evidence schema>,         # NUMERIC, COMPARISON, DIRECTION
  op: eq | gt | lt | ge | le | approx | rose | fell | flat,
  value: decimal string (with precision p), unit: percent | usd | ratio | count | ...,
  window: 1h | 24h | 7d | 30d | at_time(t) | between(t0,t1),
  cites: [ {evidence_id, field_path} ],
  text: rendered sentence (template) or free text for MECHANISM and OPINION }
```

Free text is permitted only for `MECHANISM`, `EVENT` and `OPINION` assertions, which go to the structural and semantic layers; every `NUMERIC`, `COMPARISON` and `DIRECTION` assertion is verified by code. This follows FinRobot's rule in the repository's own words: models reason, software computes. An output that states a number outside a typed assertion is rejected by the output validator.

## 4. Layers and their priority (the strictest failure wins)

| Layer | Computes | Veto |
|---|---|---|
| 1 Deterministic | asset identity through the registry (`asset_id`, quote class, kind) at every cited item; **value**: the cited field recomputed (decimal arithmetic) and compared with `value` at the stated precision (the field's number rounded or cut off at the places the assertion states; a changed digit is false, CF-64); **unit** convertible; **sign** and direction from the signed values (a `rose` needs a positive change over the window); **period**: the cited item's window and `known_at` against the assertion's window and the decision's `as_of`; **quote presence** | yes: a failure here is `REFUTES` or `WRONG_SCOPE` and nothing downstream may upgrade it |
| 2 Structural | negation, quantifier, qualifier and hedge detectors on any quoted span (not, no, none, unless, may, could, reportedly, alleged, per, over the last, so far, at least, up to) against the assertion's strength | yes: `WRONG_SCOPE(QUALIFIER)` or `REFUTES` |
| 3 Semantic | an entailment judge returns `SUPPORTS`, `REFUTES` or `NOT_ENOUGH_INFO` with a short rationale for `EVENT` assertions and free-text spans of observation class; for `MECHANISM` it decides only whether a cited source *states* the mechanism (an attribution label; the mechanism itself stays untested, contract 34.3); `OPINION` gets context only and never `SUPPORTS` | can add a failure; **can never upgrade** a layer 1 or 2 failure |
| 4 Independence | supporting items counted by origin family (data provenance, section 3) | a single-family support is shown as such |

Labels: `SUPPORTS`, `REFUTES`, `NOT_ENOUGH_INFO`, `WRONG_SCOPE(ASSET | TIME | UNIT | SIGN | QUALIFIER | MECHANISM_UNSUPPORTED)`, `PARTIAL` (names the supported and unsupported part), and `SUPPORT_UNVERIFIED` (layers 1 and 2 pass and the judge is not validated for the stratum, or no judge exists). **There is no default to supported anywhere**: the absence of a judge, a failed judge call, a timeout and an abstention all yield `SUPPORT_UNVERIFIED` or `NOT_ENOUGH_INFO`, never `SUPPORTS`.

## 5. Output and aggregation

Per assertion: label, deciding layer, reasons, the cited evidence ids with field paths. Per output: the two numbers the contract demands, kept apart: **citation exists** and **citation supports**; and the counts by label. No averaging across assertions and no single score: a receipt shows every assertion and its label, an unsupported assertion is shown and marked unsupported, counted and never used by a gate or a score (contract 18, v0.18) or flagged `UNSUPPORTED_INFERENCE`. An abstention by the agent is `NO_CALL`, not "grounded".

## 6. Validating the semantic judge (ARES-style calibration, exact intervals)

A human-labelled set of assertion and evidence pairs, two independent labellers, stratified over: correct support; outdated quote; wrong unit; wrong sign; partial statement; contradicted assertion; negation and quantification; dropped qualifier; wrong asset; wrong period; unsupported mechanism. A development part (judge prompt work) and a held-out part (never tuned on). **The size follows from the threshold and is fixed before labelling** (a later study, computed with exact one-sided 95 percent lower bounds): to show recall of at least 0.90 a stratum needs 29 failure cases with no miss or 46 with at most one; for 0.95 it needs 59 and 93; the same count of correct-support cases is needed to bound support recall; with eleven strata this is of the order of 550 pairs at 0.90, not 300 (the earlier prior of 300 or more cannot pass in any stratum). Per-stratum recall and support recall are bounded with exact (Clopper-Pearson) intervals on the held-out labels. Prediction-powered inference is used only for the population supported rate over a uniformly sampled human audit of live assertions, and **the planted calibration set never enters it** (a build-failing test; reproduced coverage 0.000 when it did). **A stratum is used only if both the validated recall on the failure labels and the validated recall on correct support reach their pre-registered thresholds and the judge's unparsable and timeout rate is bounded**, otherwise assertions of that stratum are `SUPPORT_UNVERIFIED` (a judge that never answers `SUPPORTS` must not pass). The deterministic layer 1 (arithmetic and identity) is correct by construction and its planted cases are unit tests; the layer 2 detectors (negation, hedge, sign) are heuristics, measured on the labelled set, and an unvalidated flag downgrades to `NOT_ENOUGH_INFO` (needs review), never to `REFUTES`. The tolerance of `eq` and `approx` comes from the field contract (contract section 21), never from the assertion.

## 7. Compute versus narrate (FinRobot's principle, enforced)

Anything the system can compute from evidence (a change, a ratio, a distance in ATR units, a z-score, a count of independent origins) is computed by code and enters the output as a typed value; the model may refer to it, never produce it. The numeric gate has four classes (contract 18): observed values must trace to a source field; computed values must be reproduced by the deterministic calculation with the formula version; forecast probabilities are labelled estimates checked only for format and range; risk and position values are limited by the kernel.

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| The funding example (value negative, assertion "rose") | `REFUTES` at layer 1 | planted |
| Right source, wrong period | `WRONG_SCOPE(TIME)` | planted |
| Sign dropped from a quote | `WRONG_SCOPE(SIGN)` | planted |
| Negated sentence cited for its positive | `REFUTES` | planted |
| "reportedly" cited for a flat assertion | `WRONG_SCOPE(QUALIFIER)` | planted |
| Wrong-asset item | rejected | registry fixture |
| Judge not validated for the stratum | `SUPPORT_UNVERIFIED` | validation fixture |
| Judge says `SUPPORTS` after a layer 1 failure | ignored | veto test |
| No judge configured | `SUPPORT_UNVERIFIED` (FActScore's reply parser, which counts empty and error replies as supported, is the counter-example) | configuration test; a ten-reply parser test (empty, error text, "False. ... not true", mixed) must yield `SUPPORT_UNVERIFIED` for every one |
| Output states a number outside a typed assertion | rejected by the validator | validator test |
| Held-out and development pairs share an item | build fails | dataset test |

Connection to the journey: the investigation page shows, for the decisive assertion, which layer decided it and what the cited value actually was.
