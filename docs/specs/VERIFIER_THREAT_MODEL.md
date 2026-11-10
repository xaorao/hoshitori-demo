# Verifier and ledger threat model

Scope: adversaries, forgery model, abuse cases for the trust layer. Status: DESIGNED; rows marked TESTED are covered by E1, E2a, E2b, E2c; others are required tests. Product rules: contract sections 11 (forgery model), 8, 24. Companion: `SECURITY_MODEL.md` for the rest of the system. Built since: the attacks on the time-stamp and trusted-root checks run end to end through both command-line verifiers (`tests/test_ss08_attacks.py`).

## 1. Assets and adversaries

Assets: the integrity of recorded forecasts; the claim that a record existed before its outcome window; the honesty of the displayed level; the privacy of private items.

| Adversary | Capability | Goal |
|---|---|---|
| A1 outsider | reads public data, submits records through the public interface | forge or backdate a record, inflate a score, poison the log |
| A2 careless operator (us) | bugs, restarts, mis-configuration | accidentally omit, duplicate or reorder |
| A3 malicious operator (our host compromised, or a dishonest insider) | full control of the log host, key and database, but not of the time-stamp authorities | rewrite history after seeing outcomes; backdate a favourable forecast |
| A4 colluding authorities | two tokens issued dishonestly | backdate |
| A5 malicious submitter of a Verify record | supplies fabricated evidence | obtain a valid timestamp over fabricated research and have it read as authenticity |
| A6 hostile verifier input | crafted bundles, tokens, notes | crash or fool the verifier |

## 2. What is and is not defended

| Attack | Defence | Status |
|---|---|---|
| Change a record's value after commit | leaf hash, inclusion proof, signed checkpoint | TESTED (E2c: 10 integrity cases, both verifiers) |
| Edit the checkpoint size or root, drop the signature, flip a path element, truncate a path, change the index, rename the origin | signature, 32-byte rule, size only from the signed checkpoint | TESTED (E2a, E2c) |
| Replace the log key in the policy | policy is bound out of band (published before the first scored period); a bundle with another key fails against the published policy | TESTED as a case; the out-of-band publication is a process step, NOT yet done |
| Operator rewrites a different history with the same key (A3) | independent time-stamp tokens over the original checkpoint digests no longer match; level drops below C3 and the verdict says integrity passes on the forged history but timing does not | TESTED (E2c "full operator rewrite") |
| A3 who has the key *and* obtains fresh tokens for the rewritten history after the outcome | tokens carry the real current time, later than the outcome window start: C3 fails the "before the outcome window" rule | TESTED (E2c cases on token timing) |
| Two endpoints that are one operator posing as two (A4-like weakness) | independence by root and signer fingerprint | TESTED (E2b/E2c alias pairs) |
| Weak CMS digest | SHA-1 signers rejected | TESTED (Apple case) |
| **Whole bundle substituted: attacker's own log key, records, policy and certificates (an outside review)** | trusted root from outside the bundle; the bundle policy is metadata; without a root the verdict is `BUNDLE_ASSERTED` | TESTED (E2c): fails integrity against the root; `UNAUTHENTICATED` without one |
| **A bundle forged entirely offline with self-made time-stamp authorities** | pinned authority fingerprints from the trusted root; the operator registry | TESTED (E2c) |
| Unsigned bundle metadata used for timing (the first verifier read `commit.json`) | committed time and declared window read from the signed record | TESTED (E2c; `demo_old_hole.py` shows the old verifier turning a late bundle into C3) |
| Parent and subsidiary counted as two operators | operator registry with parent and ownership evidence | TESTED (E2c); registry content: five of seven operators evidenced (E23), the rest open, ultimate ownership not established |
| A publication reference presented as proof of C2 | C2 is a claim offline and never raises a level | TESTED (E2c) |
| A malformed time string crashing a verifier | strict parsing; no timely token; never a pass | TESTED (found by a new case; fixed) |
| A token timed before the commit credited toward C3 (found by the lifecycle model; both implemented verifiers had a symmetric window and accepted tokens 30 s early) | asymmetric window `[committed_at − 1 s, committed_at + 60 s]` in both verifiers | TESTED (E2c, three new cases; the old verifiers fail the new case) |
| Replay of an old token for a new commitment | imprint binds the token to the checkpoint text | TESTED (E2b 903 hostile cases) |
| Both authorities colluding or sharing a time source (A4) | **not defended**; stated in the standing "not proven" list; mitigated by witness cosignature times (CF-13) and an optional Bitcoin anchor after the fact | documented |
| Fabricated research with a valid timestamp (A5) | the receipt shows `SUBMITTER_ASSERTED` for unverifiable evidence; no copy may imply authenticity (contract 24) | copy lint test required |
| Omission before the first append (completeness) | not defended cryptographically; the cycle manifest, raw scan response and heartbeat make omissions detectable relative to the published protocol | documented limit |
| Split view of the log to different readers | witness cosignatures (C4); without them a split view is possible and the verdict says C4 is not reached | designed, witness not joined |
| Verifier parser bugs (A6): trailing data, oversized fields, algorithm confusion, JSON key-corruption of the host runtime | strict parsers, size limits, algorithm allow-lists, own JSON parser for hashed text (CF-12) | TESTED for tokens (903 cases) and JSON (E1); full fuzzing required |
| Fork from a promoted replica or a stale primary with a reused key (review 21) | fenced promotion, epoch records with closing sizes, acceptance rule against the published chain (ADR-025) | TESTED in a model (E21); drill on the host to build |
| Rollback or rewrite of local storage below an attested checkpoint (review 21) | attested-prefix check; restore from a replica or INTEGRITY_FAILURE (ADR-026) | TESTED at file level (E20b); power loss not tested |
| Stale, forked or rolled-back trusted root; compromised old root key (review 21) | TUF-derived root update: threshold of old and new keys, exact +1, channels must agree, final root unexpired (ADR-028) | TESTED (E24); the freeze window equals the root's expiry period |
| Fake or copied ownership evidence in the registry (review 21) | structured evidence of an accepted kind bound to the chained root (ADR-029) | TESTED (E2c) |
| An endpoint that changes the operator behind it (review 21) | registry keyed by pinned root; identity read from the chain in the token (CF-38) | OBSERVED (E23) and tested (E2c alias cases) |
| Replacing the verifier itself (the website serves a bad verifier) | the verifier release is a small, versioned, hashed artefact that carries the trusted root and is published through independent channels; two implementations in two languages exist (written by one author); a third, independent one is open | designed; independent re-implementation NOT done |

## 3. The five-step forgery demonstration (from contract 11), with expected results

1. Change one evidence value.
2. Recompute every internal hash.
3. Replace the stored model response.
4. Rebuild the receipt.
5. Verify against the original external commitment.

Expected: integrity of the rebuilt record passes, reconstruction passes (internally consistent), the **external timing check fails** because the original witnessed checkpoint digest no longer matches. The Verify page says exactly this and never claims replay alone defeats a coordinated forgery. This is the demonstration; E2c's operator-rewrite case is its automated form.

## 4. Abuse cases for submitted records

Oversized or deeply nested records (limits: 1 MiB record, depth 64); a record containing a lone surrogate or duplicate keys (rejected by the record profile before append); a flood of valid records (per-identity and global rate limits; the writer refuses at back-pressure); a record that references another's `(source, id)` with different content (conflict is never appended); a Unicode confusable in an identifier (NFC and the restricted identifier grammar).

## 5. Residual risks stated plainly

Correlated failure of time sources; deletion of the log from our host (the replicas and the issuers' tokens survive, but the content does not unless replicated); our own mistakes in the policy. These are listed on the Verify page's standing list and in `OPERATIONS_ARCHITECTURE.md` (backup and restore).

## 6. Independent review

This threat model is written by its author. An independent hostile review of the trust layer is OPEN: the packet is this file, the ledger, transparency-log and verification specs, E1 to E4 scripts and results.
