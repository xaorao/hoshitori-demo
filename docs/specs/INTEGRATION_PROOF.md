# Whole-product integration proof

Scope: cross-component coherence, an outside review. Status: METHOD DESIGNED; a fixture-level harness runs (E25); the genuine proof (real RYO responses, real model calls, independent attackers) is NOT DONE and is blocked on the RYO key, a model credential and an independent reviewer. Product rules: contract sections 6, 8, 16, 25, 34, 36, 38, 44; this folder: `ARCHITECTURE.md`, `DOMAIN_MODEL.md`, `INVARIANTS.md` (N-2), `PROOF_REGISTER.md` (generated).

## 1. What is claimed, and what counts as evidence

Individual components pass their own tests; that says little about whether the product, as a connected system, shows one truth. an outside review asked for a proof that holds across every connection. The claim is seven properties, each checked at every connection of the pipeline, and each given a mutant (a deliberately broken stage) that the check must catch; a check that cannot fail proves nothing. Evidence has three grades, always named: **fixture** (a labelled synthetic input through the real code paths that exist), **genuine** (real RYO responses and real model calls through the whole system, with injected faults) and **independent** (someone other than the author attacks the integrated system). Only fixture exists today.

## 2. The seven properties and the connections they are checked on

| Property | The check | Connections (stage to stage) |
|---|---|---|
| Same truth | asset identity, quote class and instrument are identical in the evidence, the decision, the oracle series, the view model | gateway to evidence; evidence to decision; decision to settlement; decision to view |
| Same authority | no component recomputes a rule another owns; the origin time shown to a user is the one settlement uses | decision to view; decision to settlement; claims to research (next check) |
| Same history | the hash of the committed record bytes is identical in the log, the verifier output, the view model and the settlement input | decision to ledger; ledger to bundle; bundle to verifier; ledger to view; ledger to settlement |
| Same failure | an unavailable, stale or invalid source gives the same honest state at every stage and no number appears | gateway to evidence to decision to ledger to view to settlement |
| Same privacy | a private sentinel never reaches the log, the bundle, a public view, a cache, a share card or a news-search request; it reaches only the model recipient | evidence to every egress and every public surface |
| Same outcome | the probability and event shown are the ones committed and the ones scored | decision to view; view to settlement |
| Same reasoning | the explanation, the next check, the evidence challenge and the final action use one decision contract and one next-check function | claims to research to view; challenge to decision |

## 3. How each property is made checkable, and the mutant that breaks it

The pipeline is run once honest and several times with one stage replaced. A property passes only if the honest run satisfies it and its mutant fails it. In E25 the mutants are: the view model lower-cases the asset id (same truth); the view model computes its own origin rule (same authority); the view model hashes a re-serialised record (same history); the decision stage turns an unavailable value into zero (same failure); a private text reaches the ledger record, or reaches the news-search request (same privacy; two mutants); the view rounds the probability (same outcome); the view drops one evidence id (same reasoning); settlement reads the series of another quote class (same truth at a second connection). Each mutant is a mistake a real implementer makes.

## 4. The genuine proof (to run once the blockers are lifted)

1. **The signature journey, once, end to end:** one real RYO research decision from a real catalogue call; one evidence challenge with its controls; one forecast committed with two real counted tokens from operators with ownership evidence; one offline verification by a verifier that did not take part in the build; one outcome settled by the independent settlement implementation. All seven properties are checked at every connection of that one journey, and the record of the check is itself committed.
2. **Faults inside the journey, one at a time, each followed by all seven checks:** a crash between every pair of stages; a delayed acknowledgement; a duplicated delivery; a stale replica promoted while the primary lives; a failed or slow time-stamp service; a source that returns `partial` or `unavailable`; a source that changes a value between two reads; an adversarial evidence edit; a restored older backup; a clock offset above the limit.
3. **Private data:** a sentinel in a private claim travels the whole journey, including model calls, caches, share cards, exports and verification bundles.
4. **Agents and interface:** Track 1 shows the arms against the baselines on pre-registered tasks (`LEAGUE_EXPERIMENT.md`); Track 2 shows the comprehension studies (`UX_SPEC.md`, `ACCESSIBILITY_SPEC.md`).
5. **Independent attack:** at least two reviewers other than the author attack the integrated system and its stated assumptions (`REVIEW_PACKET.md`); their findings are recorded verbatim.

If a guarantee fails the smallest mechanism that caused it is repaired and its dependent checks rerun; nothing is declared passed because a document describes it.

## 5. What the fixture harness shows and does not show

E25 shows that the method works: on one synthetic, labelled fixture the honest pipeline satisfies the seven properties in a normal run, an unavailable-source run and a private-input run, and 9 of 9 planted mutants are caught. It shows nothing about a real RYO response, a real model, real storage, the real interface, concurrency, or users. It is a harness to be reused with the real stages as they are built, not evidence that the product is coherent.

## 6. Recording a result

Every critical guarantee has a row in `PROOF_REGISTER.md`: the claim, the status (PASSED with its grade, FAILED, or NOT VERIFIED), the reproduction command, the results file, a hash of the source files of the experiment (the research folder is not under version control, so a hash stands in for a commit), and the known limit. The register is generated by `../tools/build_proof_register.py` from the results files and fails if a results file no longer matches the claim it supports.
