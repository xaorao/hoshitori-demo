# What an offline pass proves, in three stages (threat analysis before any verifier change)

Owner: Verification and Security (the verifier, the bundle and the Verify page). Status: stage A and the lifecycle half of stage B are built; in stage C the whole-batch part is built in both verifiers, in the export and on the Verify page; the later checkpoint with the origin, the ending step and the settlement record is built in both verifiers and in the export. The staged approach was chosen over a single large change.

Rule it answers: a verifier change that is not the closing of a forgery someone has shown needs a written threat analysis and a failing check first. This document is the analysis. Each stage lists what changes, the attack it closes or the claim it makes true, what could go wrong, and the checks that must fail first.

## What is wrong now (all reproduced against the build copy)

| Gap | Finding | Where |
|---|---|---|
| G1 | The page says "Passed: independently time-stamped before the outcome window", but no product record declares the window, the origin step is appended after the stamped checkpoint and the settlement comes later, so no bundle holds either. | `labels.TRUST_PHRASES`, `web/src/format.ts`, the Verify page, `verify.py` |
| G2 | A cycle whose tokens were never kept shows the C3 phrase on every page; a bundle whose tokens are damaged verifies at C1 while the page stays at C3. | `view_models`, `deployment.export_site` |
| G5 | A bundle missing one forecast of its cycle gets the same verdict as the whole one; adding the cycle's manifest drops a valid cycle to C1 because the manifest is older than the token window. | `verify.py`, `bundle.py` |
| G7 | The lifecycle accepts a C3 slot whose origin is before attested time plus u. | `lifecycle.py` |
| G8 | The verdict does not say which operator count, window and u the trusted root used, so a loose root gives a quiet C3. | `verify.py`, `verifier-js` |
| G13 | The contract says fewer than two counted tokens gives C2; the code, CF-48 and the verification spec say C1. | contract sections 2 and 8 |

## Stage A: say exactly what is proven, and name the basis

Changes:
1. The C3 phrase becomes what the check shows: the records existed as written no later than the time-stamps say. The words "before the outcome window" go until stage B can show the window.
2. Both verifiers add a `timing.basis` object to the verdict: the root's required operator count, the counting window before and after the commit, the attestation close, the largest spread and the uncertainty constant `u`, each as the root states it. A page shows it beside the level.
3. The contract text for fewer than two counted tokens is corrected to C1 (the code and CF-48 are the authority).

Closes: G1 (wording), G8 (a quiet loose root), G13.

Threats considered:
- A hostile bundle supplies its own policy: unchanged, the basis comes only from the trusted root supplied from outside (CF-24); a policy file in the bundle is ignored (existing case).
- Both verifiers disagree on the basis: a differential test compares the two on every case of the shared case list.
- A reader takes the basis as a promise: it is printed as "the root asked for", not as a result.

Checks first: the phrase tests (`labels_checks`, `format.test.ts`, `states_test`, `verify_flow_test`) fail on the new words; a verdict test requires `timing.basis` with exact values on the honest case and on a root with a looser rule (`window_after_commit_s` raised), in both verifiers.

## Stage B: derive the earliest origin; refuse an earlier one

Changes: the verifier derives, for each forecast, the earliest possible origin from the tokens and the root's `u` and spread (`eligible_after`, then the first one-minute candle closing after it), prints it in the verdict, and the lifecycle refuses a C3 slot whose origin is earlier than the derived value (G7). The page may then say "the outcome window could not have opened before T".

Closes: G7, and G1 in part: the earliest start becomes a derived fact rather than a declared one. It does not show when the window did open (that is stage C).

Threats: an operator who sets an early origin is caught at the lifecycle and by the verifier's own derivation; a verifier that derives a different value from the operator is a defect found by the differential test; clock skew between the verifier and the stamps does not enter (derivation uses only stamped times and the root's constants).

Checks first: a lifecycle case with an origin one second early is accepted today and must be refused; a verdict case where the page's origin and the derived one differ.

## Stage C: an extended bundle

Changes: a bundle format version 2 that carries, under a later stamped checkpoint, the cycle's manifest, the batch root, the origin step and the settlement, and the list of the cycle's forecasts (so completeness is checkable). The window rule applies to forecasts only; earlier records need only inclusion under a stamped checkpoint. The verifier checks the origin is at least the derived earliest and that the forecasts present are the cycle's complete set; a bundle missing one is not scorable. Both verifiers read both formats; a version-1 bundle keeps its stage A and B verdict and is marked as not carrying the cycle's completeness.

Closes: G1 (the window is in the bundle), G5 (completeness and the older manifest), G2 in part.

Threats: dropping a forecast from a bundle (caught by the cycle's declared count under a stamped checkpoint); substituting a later checkpoint that omits records (a consistency proof from the earlier checkpoint is required); downgrade to version 1 to avoid the completeness check (the verdict states what the format did not carry, and a scored comparison requires version 2).

Checks first: the three reproduction scripts for G1, G5 and G2 become cases of the shared list and fail until the stage is built.

## What none of the stages claims

That the time-stamp authorities were honest and independent at once, that the evidence was true, or that the operator ran one writer: the standing not-proven list stays on every verdict.

## Stage C in detail (what is built, in parts, each with its checks first)

**What the league already holds that makes this possible.** A cycle's batch is submitted and appended together, so its records are a contiguous run of the log under one signed checkpoint (the one the time-stamps stamp). `batch_root` (the Merkle root of the digests of the batch's records in submission order, `league.batch_root`) is named by the cycle's COMMITTING step, which is appended **after** the stamped checkpoint, together with the slots' COMMITTED, AWAITING_ORIGIN and later steps and the settlement. Consistency proofs between two checkpoints exist in both languages (`merkle`).

**Format version 2 of a bundle** (each item's proof carries its own signed checkpoint, so a bundle can hold two checkpoints without anything new):

- `format.json`: the number 2.
- **The whole batch** under the stamped checkpoint: the cycle's COMMITTING step is the last record of the committed batch, and the `batch_size` records before it are the members its `batch_root` covers (built). So completeness needs the stamped checkpoint alone.
- **Later items** (a second part, built after the first): records proven under a later signed checkpoint of the same log: the cycle's COMMITTED step, the slots' AWAITING_ORIGIN, OPEN and settlement steps and the settlements, with `consistency.json` holding the consistency proof from the stamped checkpoint to the later one. A later checkpoint is accepted only if signed by a log key of the trusted root whose era covers its size and the proof verifies; at most two checkpoints are allowed.

**What a verifier then derives, and says (the `cycle` part of the verdict):**

1. `complete`: the digests of the batch's records, in log order (the last `batch_size` under the stamped checkpoint, all present in the bundle), make exactly the `batch_root` that the cycle's COMMITTING step (proven under the later checkpoint) names. A forecast left out, added or changed changes the root. This closes G5 (a bundle missing a forecast looks like the whole) and G2 in part.
2. For each forecast: the earliest origin derived from the tokens (already in the verdict as the eligible offset), the eligible time and the origin of its lifecycle steps (AWAITING_ORIGIN, OPEN), and whether the recorded origin is not before the derived one. This is G1 for the window: the page can then say the call's window opened after the time-stamps.
3. Whether a settlement record exists for the forecast and the state it names, and (built) whether it agrees with itself: one record, the members the writer writes, the forecast it names, the step that cites it, the directional rule applied to its own two prices and the time the call's window closes. Never whether the prices are the market's or the settlement is right: the independent settlement check is a separate thing.

**Eligibility** (`scorable`): a version-1 bundle carries none of this, so it keeps its stage A and B verdict and gains the reason `COMPLETENESS_NOT_SHOWN` (a scored comparison requires version 2); a version-2 bundle is scorable only if it is complete and no origin is before its derived earliest. The reasons are `ROOT_NOT_CURRENT`, `INTEGRITY`, `LEVEL_BELOW_C3`, `UNRESOLVED_REFERENCE`, `COMPLETENESS_NOT_SHOWN`, `NO_FORECAST` (built), `ORIGIN_BEFORE_ATTESTATION`, `DATA_MODE`, in that order in both verifiers.

**Threats considered for this part.**

- *A later checkpoint that omits records or rewrites the cycle:* needs a consistency proof from the stamped checkpoint; one that rewrites an earlier record has none that verifies.
- *A later checkpoint signed by an old or foreign key:* the signature is held to the eras of the trusted root, and the era must cover the later size.
- *A bundle that drops the later part to hide a bad origin:* it is then a version-1 bundle, not scorable.
- *A bundle that adds a forecast the cycle did not make:* the batch root no longer matches.
- *A COMMITTING step that names another batch:* it is proven under the later checkpoint, which the consistency proof ties to the stamped one; the digests must still reproduce its root.
- *The two verifiers disagreeing:* the shared case list covers every case in both, and the recorded corpora are replayed by the Node side.

**Order of work.** part 0 the cycle step names `batch_size`; part 1 completeness from the stamped checkpoint in the Python verifier; part 4 the same in the Node verifier and the corpora (done with part 1, so the two never differ); part 5 the league export (the whole batch and the committing step), the Verify page words and the page tests; part 2 the later part with the consistency check, and part 3 the origin and settlement facts (open). Each is its own unit with its failing checks.


## Stage C, parts 2 and 3: the later part and what it shows (specified before the code)

**What a bundle may now hold.** The records of the batch are proven under the stamped checkpoint, as before. A bundle may also hold records that the log appended after the batch (the forecasts' COMMITTED and AWAITING_ORIGIN steps, later the OPEN step and the settlement), each proven under one later signed checkpoint of the same log, and one more file at the root of the bundle: `consistency.json`, canonical JSON with the members `format` (the number 1), `from_size`, `to_size` and `hashes` (the RFC 9162 consistency path, each hash as 64 lower-case hexadecimal digits). At most two distinct checkpoints appear in a bundle.

**What the verifier does.**

1. *One checkpoint:* as before. *Two checkpoints:* the bundle must hold a readable `consistency.json` whose `from_size` is the size of the smaller checkpoint and `to_size` the size of the larger, and the path must verify between the two roots. Then the smaller one is the *stamped* checkpoint and the larger the *later* one. A missing or unreadable file leaves the checkpoints differing (`CHECKPOINTS_DIFFER`); a file that is present but wrong (sizes that do not match the two checkpoints, more or fewer hashes than the proof needs, a path that does not reproduce the later root) is `CONSISTENCY_FAILED`. Three or more checkpoints are `CHECKPOINTS_DIFFER`.
2. Both checkpoints are held to the trusted root as every item's is (a log key whose era covers the checkpoint's size). The tokens stamp the digest of the *stamped* checkpoint's text and nothing else.
3. The window rule applies to the records under the stamped checkpoint only. A record under the later checkpoint is a later fact of the log, not something the tokens vouch for, so it takes no part in the level and its commit time is not judged against the token window. The verdict names the records of each group.
4. `integrity.ok` needs every item to verify and, with two checkpoints, the consistency to verify.

**What the later records show (part 3).** For each forecast record under the stamped checkpoint (its `id` is its forecast id), the later group is read for the forecast's steps (`forecast_state` records with that `forecast_id`):

- the COMMITTED step: its level;
- the AWAITING_ORIGIN step: `attested_at`, `uncertainty_ms`, `spread_ms`, `eligible_after`;
- the OPEN step: `origin`;
- an ending step (SETTLED, AMBIGUOUS, VOID or UNSETTLEABLE): its state, and nothing about whether the settlement is right.

The verifier derives, from the tokens and the root, the earliest instant a call may be eligible: the attested time of the forecast's own timing result plus `u` plus the spread, as the lifecycle computes it, and the earliest origin, the first minute close strictly after it. It reports per forecast `eligible_ok` (the step's `eligible_after` is not earlier than the derived one) and `origin_ok` (the OPEN step, when there is one, names an origin not earlier than the derived origin and equal to the first minute close strictly after its own `eligible_after`).

**Eligibility.** A new reason, `ORIGIN_BEFORE_ATTESTATION`, is given when any forecast's `eligible_ok` or `origin_ok` is false. It sits between `NO_FORECAST` and `DATA_MODE`. A bundle with no later part shows nothing about origins and gains no reason from it: the verdict says `later: shown false`, as version 1 does for completeness. A forecast of the stamped group with no AWAITING_ORIGIN step in a bundle that does hold a later part is reported as `awaiting: absent` and counts as a failure of that forecast, because the bundle then shows nothing about when it became eligible.

**Threats considered.**

- *A later checkpoint that rewrites or omits earlier records:* it has no consistency proof from the stamped checkpoint that verifies; the bundle is not intact.
- *A later checkpoint with the wrong sizes, or a proof for other trees:* `CONSISTENCY_FAILED`; the sizes in the file must be those of the two checkpoints in the bundle, and a proof that is padded or short is refused.
- *A later record that makes an origin look late enough:* it is read, not trusted: the eligible time and the origin are compared with what the tokens give, and an earlier one fails.
- *Hiding a bad origin by dropping the later part:* the bundle then shows nothing about origins; what it shows is stated, and a scored comparison that needs origins requires the part.
- *A step for a forecast the batch does not hold, or two COMMITTED steps for one forecast:* ignored for the forecast it does not name; a second step of the same kind for one forecast makes that forecast `awaiting: conflicting` and fails its origin check.
- *The later checkpoint signed by another key:* the era rule of the trusted root applies to it exactly as to the first.
- *The two verifiers disagreeing:* every case below runs in both, and the verdicts must be equal field by field.

**Cases (written first, each failing until built).** A world with a later part and a good consistency proof passes and shows the facts; the same with a flipped hash in the proof, with the sizes swapped, with the file missing, with a third checkpoint, with a later checkpoint signed by an unknown key, with an AWAITING_ORIGIN step whose eligible time is one millisecond too early, with an OPEN step whose origin is one minute too early, with an OPEN step whose origin is not the minute close after its own eligible time, with a record of the later group that changes an earlier record's content (the proof then does not verify), and with a bundle that holds later records but whose tokens stamp the later checkpoint (refused: the tokens must stamp the smaller one).

**Order of work.** the cases and the Python verifier; the Node verifier and the equal-verdict test; the bundle writer and the league's export; the Verify page words and the page tests; the corpora.

**Built** (this stage, parts 2 and 3): the cases (shared by both verifiers and run against each), both verifiers, the bundle writer, the league's export of the later part, the refresh of a site's bundle when a later checkpoint appears, the Verify page's finding and its reworded lists, and the Node corpus. Not built: the settlement record itself in the later part (the ending step's state is shown, not the record), a later part for a cycle whose ledger has signed no later checkpoint yet (the bundle shows none until the next commitment or the writer stops).
