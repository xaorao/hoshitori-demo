# Transparency log specification

Scope: transparency log, witnessing and the C4 level. Status: DESIGNED; the format and verifiers were built and tested as spikes (E2a, E2c); the witness and public-log parts are NOT built and not published. Product rules: contract section 8 (levels C0 to C4), 11, 24. Sources studied: C2SP specifications (`tlog-checkpoint`, `tlog-cosignature`, `tlog-witness`, `tlog-proof`, `tlog-policy`, `signed-note`; commit fea9004), RFC 9162 (read), Tessera (Apache-2.0, README and storage drivers listed: GCP, AWS, POSIX, MySQL; witness policy support), transparency-dev `witness` (Apache-2.0, README read), Sigstore Rekor v2 (`rekor-tiles`, Apache-2.0, `rekor-tiles/CLIENTS.md` and API listing read), OpenTimestamps (client and calendar protocol; LGPL client, ideas and optional unmodified use only). Built since: the checkpoint, proof and bundle formats and both verifiers are product code and TESTED (see `../STATUS.md`); witnesses and the public log remain unbuilt.

## 1. Decision (ADR-003)

Run **our own small log** in the C2SP tile-compatible *formats* (checkpoint, tlog-proof, signed-note, tlog-policy) with an implementation of our own (the append-only JSONL segments and rebuildable index of the ledger spec), rather than adopting Tessera or Rekor as the primary store. Reasons, each measured or sourced:

1. The expected load is tiny (thousands of records a day; E3 measured about 5,300 durable commits per second on one core). Tessera's value is scale and cloud storage drivers; the cost is a Go service, a storage backend and a second language in the trust path. A library `transparency-dev/merkle` is used only as a **test oracle** (E2a: Python, JavaScript and Go agreed on 2,485 + 2,485 exhaustive proofs and 15,128 mutations).
2. The checkpoint, proof and policy *formats* are what an outsider verifies. By following C2SP exactly (E2c) any other C2SP verifier can check our bundles, and we can later move to Tessera without changing a byte of the published artefacts.
3. A log we operate is **not** an independent witness. Independence comes from (a) time-stamp authorities we do not control, (b) witnesses we do not control cosigning our checkpoints, (c) an independently operated public log receiving our checkpoint digest. The design therefore does not depend on trusting our operator; what our operator controls is availability and the exact bytes, which the outside commitments bind.

Rejected alternatives: Rekor v2 as the only log (it returns **no time**: `integrated_time` is 0 and clients must use RFC 3161 tokens, `rekor-tiles/CLIENTS.md`; and every entry becomes public and permanent, which the private-by-default rule of contract section 36 forbids for most records); Tessera on POSIX (a good fit later; adopt if any need for tile serving or higher volume appears); a blockchain anchor as the primary mechanism (OpenTimestamps and Bitcoin remain an optional after-the-fact anchor, never a timing level).

## 2. What is public, and what is not

- Private by default: records of private investigations are never in the public log; only a salted commitment digest may be (contract 36.3). The public log holds forecast commitments, checkpoints, settlements and challenge records that were published.
- The log is append-only and public to read. Entry bodies are canonical records (ledger spec section 2); raw third-party payloads are referenced by hash and held under the data-rights rule.

## 3. Components and their trust

| Component | Trusted for | Not trusted for |
|---|---|---|
| Our log (writer, key) | ordering and availability | the time at which anything happened |
| RFC 3161 authorities (at least two operators, independence by root and signer fingerprint, CF-02) | the time of a digest | the content, or both being honest at once |
| Witnesses (cosigning checkpoints, C2SP `tlog-witness`) | refusing to cosign two inconsistent histories | liveness |
| Public log (Rekor v2 or similar) for C4 | independent, public inclusion of the checkpoint digest (no time) | time |
| OpenTimestamps / Bitcoin (optional) | an unforgeable *later* anchor | being early |

## 4. Witnessing and C4

- **Witness protocol:** a witness checks that each submitted checkpoint is an append-only evolution of the one it last cosigned (consistency proof, RFC 9162) and returns a `cosignature/v1` line. The note then carries our signature plus witness cosignatures; the **policy** (tlog-policy format) says which log key and how many of which witnesses make a checkpoint acceptable.
- **Joining the public witness network is outward-facing and permanent (the log name and key become known) and is held for the project owner's decision.** Until it happens, C4 is unavailable and the verifier reports C3 at most. A witness we ourselves run counts for nothing (contract 8: witnesses must be operated by parties we do not control).
- **C4 definition after CF-01:** included in an independently operated log that is cosigned by witnesses we do not control. It adds ordering and public discoverability; it adds no time. A cosignature time (CF-13) may be stored as supplementary evidence and never replaces the tokens.
- **Public commit (C2) is a claim.** Publishing the checkpoint digest somewhere public (a post, a repository commit) gives visibility; offline it is unauthenticated and untimed, never raises a level and never counts toward headline eligibility (verification spec step 7).
- **Rekor v2 use:** optional; we would submit the checkpoint digest (never record bodies). **Publishing a test hash is held** (permanent public record). Until approved, Rekor behaviour is documented from its client guide only.

## 5. Checkpoint, proof and policy formats (as built in E2c)

Identical to the ledger spec section 5 and 6; additions here: the **trusted root** (the verifier's trust anchors; the bundle's `policy.json` is metadata only and is never believed, verification spec section 2; the trusted root ships with the verifier release and is published through independent channels before the first scored period) lists the log origin and key, the accepted time-stamp roots and operator labels, the digest and signature algorithm allow-lists (SHA-256 or stronger for the CMS digest; SHA-1 signers rejected, CF-04), the witness set and quorum, the allowed clock skew between `committed_at` and the token times (60 s), and the version. A change of policy is a new policy version recorded in the log, signed by the previous key; verifiers pinned at the genesis root follow the chain. Each key, operator and witness carries a validity range. The operator registry (parent organisation and ownership evidence) covers time-stamp authorities, witnesses and public logs alike, so one organisation cannot appear as several independent operators in different roles.

## 6. Failure modes

| Failure | Behaviour | Test |
|---|---|---|
| Log operator rewrites history (E2c "full operator rewrite") | integrity of the forged history passes, the independent tokens no longer match the new checkpoint digests: level falls below C3 and the verdict says so | E2c case, both verifiers |
| Whole bundle substituted, or a log forged with self-made authorities | the verifier requires a trusted root from outside the bundle; without one it says the bundle supplied its own anchors | E2c cases (pinned: integrity fails; unpinned: UNAUTHENTICATED) |
| Split view (two checkpoints for one size) | the writer refuses; a witness would refuse; a verifier given both reports `INCONSISTENT_CHECKPOINTS` | fixture |
| Witness unreachable | cosignature missing; level stays at C3; nothing blocks the cycle | fault injection |
| Time-stamp authority unreachable | other authority; if only one, C2 with one token noted | fault injection |
| Key compromise | new key era with both signatures; tokens bound earlier history; verifier flags the era | rotation fixture |
| Replica promoted while the primary lives; stale primary keeps signing with the same key (review 21) | epoch compare-and-swap and continuity proof for the new writer; epoch record with the closing size; verifiers accept a checkpoint only if consistent with the published chain and not larger than the closing size of its epoch's key | E21 model: 0 forks among accepted checkpoints in 1,500 schedules; the file-lock design forked in 1,103 |
| Local storage rolled back below a checkpoint that has tokens | INTEGRITY_FAILURE or restore from a replica with the attested root; no append | E20b |
| Stale trusted root, forked root channels, rolled-back root chain | EXPIRED, FORK or the update is rejected; the verdict is never scorable on an expired root | E24, 23 cases |
| Public log unavailable | C4 not reached; recorded as pending | fault injection |

## 7. Tests and evidence

E2a (three-way agreement), E2b (nine live authorities; OpenSSL against our verifier; 903 hostile token cases, 0 disagreements), E2c (<!--n:e2c-->48<!--/n--> bundle cases in two languages, 0 disagreements and 0 expectation failures) are re-runnable scripts and the seed of the suite. **Not verified:** behaviour against a real witness; a long-running log; the Go Tessera library as a drop-in for our published artefacts (format claim only).

Connection to the journey: the "independently timed record" step; this document is why a visitor can check, from a downloaded bundle and no network, that the thesis existed before its outcome window.
