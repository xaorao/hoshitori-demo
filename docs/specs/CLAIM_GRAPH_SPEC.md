# Claim graph specification

Scope: claim decomposition into typed argument graphs. Status: DESIGNED. Product rules: contract sections 18, 34 (the graph), 35 (preliminary versus completed), 51 (the person's reading). Sources studied: FActScore (code read: `atomic_facts.py`, `factscorer.py`; MIT), FEVER (label scheme and annotation guidelines; Apache-2.0), ARES (judge validation; Apache-2.0), TruLens (groundedness feedback source; MIT), Nota (`news_verify`, citation-path handling; no licence, ideas only). What each gets wrong for this task is in the grounding contract, section 2; this file is the graph itself.

## 1. What a claim graph is

A claim graph is a small directed structure built from one piece of text, in which every node carries an exact span of the original text, a type, and a status, and every edge is either a dependency or an author-stated causal link. It exists so that a composite claim ("whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week") is never given one verdict: the observation, the interpretation, the causal step and the forecast are judged separately, and **support never propagates along an edge** (contract section 34.4).

## 2. Schema

```
ClaimGraph {
  graph_id, text_sha256, text (private, NFC),
  extraction_runs: [ {run_id, model_call_ids[], prompt_hash} ],         # 3 independent runs
  nodes: [ {
    node_id, type: OBSERVATION | INTERPRETATION | CAUSAL_LINK | FORECAST | CONDITION |
                   OPINION | PROMOTION_SIGNAL | CORRECTION_REF | ASSUMPTION_INFERRED,
    source_span: {start, end, text},          # exact substring of NFC text; IMMUTABLE; absent only for ASSUMPTION_INFERRED
    requires: [node_id],                       # for ASSUMPTION_INFERRED: the stated nodes that need it
    asset_ref: {token_id?, instrument_id?, status},
    user_reading: {text, edited_at} | null,    # separate record; never replaces source_span
    status: <vocabulary of the node type>, status_evidence: [evidence_id],
    stability: STABLE | UNSTABLE | DISPUTED | NOT_VALIDATED } ],
  edges: [ {from, to, kind: DEPENDS_ON | CAUSAL | CONDITIONAL_ON} ],
  decomposition: {state, stability_score, segmenter_disagreements[]} }
```

Statuses by node type, the propagation rules and the weakest-link sentence are the contract's (34.3, 34.4); this file implements them as a pure function `propagate(graph) -> graph` with no model call.

## 3. Pipeline

| Step | Computation | Output | Failure |
|---|---|---|---|
| 1 Normalise | NFC; reject control characters; length cap | text | refuse with the reason |
| 2 Segment (deterministic) | sentence and clause boundaries; causal and conditional connectives (therefore, because, so, if); modal verbs (will, may, could); numbers with units; asset mentions through the registry | candidate spans, no types | none (pure) |
| 3 Extract (model, typed, three independent runs) | schema-bound extraction; at most 8 nodes | node lists | `MODEL_OUTPUT_INVALID`; a made-up decomposition is never shown |
| 4 Validate spans | every stated node's span is an exact substring of the NFC text; a failing node is rejected and the graph flagged | graph | `DISPUTED` if rejections change the graph |
| 5 Compare | match nodes across runs by span overlap (intersection over union at least 0.5); stability score = share of nodes matched in all runs; compare with step 2 | `stability`, `segmenter_disagreements` | `UNSTABLE`, `DISPUTED` |
| 6 Infer premises | a model proposes `ASSUMPTION_INFERRED` nodes citing the stated nodes that need them; shown as "we think this is implied" | nodes | optional |
| 7 Person confirms or edits | edits create `user_reading`; spans unchanged; only affected checks re-run | graph version | none |
| 8 Plan checks | per node, registered checks by type and asset (the next-check function) | `Check` list | `NOT_CHECKABLE` with the reason |

The preliminary page is built from the first extraction and step 2 (contract 35); runs two and three arrive as a visible update. Nothing in this pipeline marks a node with a check outcome.

## 4. Stability and validation (contract 34.2, 34.11)

- **Stability** detects an extractor that wavers; it is not evidence of correctness (three runs of one model share blind spots). Threshold 0.7 (prior) is set from the labelled set, not from the extractor's agreement with itself.
- **The structurally different second reader** (step 2) does not use a model. A causal connective without a `CAUSAL_LINK` node, a number without a node, a node span that crosses two clauses are disagreements, listed to the person; they never change the graph silently.
- **States:** `STABLE`, `UNSTABLE`, `DISPUTED`, `NOT_VALIDATED` (until the labelled set exists the page says so).

## 5. The labelled set (the empirical gate)

100 or more composite claims (a prior), including adversarial ones: nested conditionals, claims quoted from third parties, negations, sarcasm, claims with no testable content, a causal word that is only a conjunction, claims in which the asset is ambiguous. **Two independent annotators**, instructions written before annotation, disagreements adjudicated by a third and kept. Annotators label: node spans (character offsets), node types, edges, which premises are implied but unstated. Metrics: span exact match (hard rule: any model node without an exact span is a defect, so this is 100 percent by construction and measured on what survives), node-type accuracy, missing-premise rate, spurious-node rate, edge accuracy, each with an interval; inter-annotator agreement (Cohen's kappa for types, F1 for spans). A development part tunes prompts; a held-out part is never used for tuning. The first golden case is the composite example above (three stated nodes, one causal link, one inferred identity premise).

## 6. Propagation as code

`propagate` implements, for every node, in dependency order: the type's status vocabulary; `PREMISE_CONTRADICTED` if a required premise is contradicted; `RESTS_ON_UNPROVEN_PREMISE` if a required premise is unproven, not found or not checkable; `ORDER_CONSISTENT` / `ORDER_INCONSISTENT` for causal links whose cause and effect are both observed (never "supported"); the headline is a template filled from the statuses that names the weakest required link. Property tests: no status of a node depends on a status of a node it does not require; adding a supported node never changes another node's status; a contradicted premise marks every dependant; the headline names the first weakest link in dependency order.

## 7. Storage, privacy and entry points

Graph records are private by default (claim text is a private payload; contract section 36), the ledger holds the digest; publishing a graph is a per-artifact action with a preview of exactly what becomes public. Entry points: the paste box, the Voices and News surfaces, the Evidence Challenge Engine and the `research_integrity_check` skill all call the same `build_graph` and `propagate`; none has its own parser (contract 34.8). A forecast node becomes a contract sketch with every missing parameter listed as a choice the person must confirm (34.9).

## 8. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| A model node whose span is not found | rejected, graph flagged | span fixture |
| An instruction inside the claim text | ignored; recorded `UNTRUSTED_INSTRUCTION_IGNORED` | injection suite |
| Three runs disagree | `UNSTABLE`; "decomposition uncertain" and edit invited | stability fixture |
| The person edits "may" into "will" | audit labelled "your reading"; `source_span` untouched | edit fixture |
| Extraction fails | "This claim could not be read automatically"; no invented graph | failure fixture |
| Ambiguous ticker | node `asset_ref.status = AMBIGUOUS`; checks that need the asset are `NOT_CHECKABLE` | registry join |

Connection to the journey: this is the first step of "one claim": what the visitor pastes becomes a graph with a plain-language preliminary page within a few seconds (the 5-second median is a prior to be measured; model latency is unmeasured).


## Rules from an earlier study

1. `source_span` is an ordered list of immutable segments, each an exact substring; coreference (which asset "it" means) is a separate recorded step that sets `asset_ref` and never rewrites a span.
2. An inferred or edited node may not introduce a number, date or asset absent from the spans it cites unless it is marked as the person's own value.
3. An interpretation node reaches `SUPPORTED_BY_EVIDENCE` only from evidence of an allowed class (a disclosure, a primary document, an on-chain record of identity); the observation's own evidence id never does.
4. The labelled decomposition set has a minimum count per node type and edge kind and a pre-registered agreement floor per type pair; types that cannot be told apart are merged for display.
