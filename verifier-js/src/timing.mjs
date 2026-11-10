// Which time-stamp tokens count, how many independent operators they come from, and the level they support.
// Second implementation of src/hoshitori/timing.py. Times are milliseconds since the epoch.
// A record that says it was created after it was committed is in an impossible order (IMPOSSIBLE_ORDER) and gets no counted
// token (contract section 2: as_of <= created_at <= committed_at); a token that precedes the record's own created_at does not count
// (VERIFICATION_SPEC section 7). Both allow the clock-offset limit that opens the counting window (windowBeforeCommitS, 1 s).
export const LEVEL_C1 = "C1";
export const LEVEL_C3 = "C3";
export const IMPOSSIBLE_ORDER = "IMPOSSIBLE_ORDER";

// The words for IMPOSSIBLE_ORDER, the same in both verifiers.
export const orderDetail = (rules) => `created_at is more than ${rules.windowBeforeCommitS} s later than committed_at`;

export function evidenceParent(operator, rules, rootFingerprint) {
  if (!operator || operator.evidence === null) return null;
  const proof = operator.evidence;
  if (!rules.evidenceKindsAccepted.has(proof.kind) || !proof.ref || proof.subjectSha256 !== rootFingerprint) return null;
  return operator.parent;
}

function independent(tokens) {
  const usable = tokens.filter((t) => t.parent !== null);
  if (usable.length === 0) return 0;
  return Math.min(new Set(usable.map((t) => t.signerFingerprint)).size, new Set(usable.map((t) => t.parent)).size);
}

// `tokens` are { name, verified: {genTimeMs, operator, rootFingerprint, signerFingerprint} | null, reason }.
export function assess(committedMs, windowOpensMs, tokens, rules, createdMs = null) {
  const before = rules.windowBeforeCommitS * 1000;
  const after = rules.windowAfterCommitS * 1000;
  const uncertainty = rules.uConstantS * 1000;
  const impossible = createdMs !== null && createdMs > committedMs + before;
  const assessed = tokens.map((item) => {
    const proof = item.verified;
    if (proof === null) return { name: item.name, valid: false, reason: item.reason ?? "rejected", genTimeMs: null, rootFingerprint: null, signerFingerprint: null, operator: null, parent: null, timely: false };
    let timely = committedMs - before <= proof.genTimeMs && proof.genTimeMs <= committedMs + after;
    if (createdMs !== null) timely = timely && createdMs - before <= proof.genTimeMs;
    if (windowOpensMs !== null) timely = timely && proof.genTimeMs + uncertainty < windowOpensMs;
    if (impossible) timely = false;
    const parent = evidenceParent(rules.operators.get(proof.operator), rules, proof.rootFingerprint);
    return { name: item.name, valid: true, reason: null, genTimeMs: proof.genTimeMs, rootFingerprint: proof.rootFingerprint, signerFingerprint: proof.signerFingerprint, operator: proof.operator, parent, timely };
  });
  const counted = assessed.filter((t) => t.timely);
  const independentOperators = independent(counted);
  const chosen = bestSet(counted, rules);
  const declared = windowOpensMs !== null;
  const assurance = `TA-3 (assumed clock uncertainty u=${rules.uConstantS}s plus observed spread; not proven)`;
  const error = impossible ? IMPOSSIBLE_ORDER : null;
  if (chosen.length === 0) {
    return { tokens: assessed, independentOperators, chosen: [], spreadMs: null, attestedAtMs: null, level: LEVEL_C1, eligibleAfterOffsetMs: null, windowDeclared: declared, assuranceClass: assurance, error };
  }
  const times = chosen.map((t) => t.genTimeMs);
  const spread = Math.max(...times) - Math.min(...times);
  return { tokens: assessed, independentOperators, chosen: chosen.map((t) => t.name), spreadMs: spread, attestedAtMs: Math.max(...times), level: LEVEL_C3, eligibleAfterOffsetMs: rules.uConstantS * 1000 + spread, windowDeclared: declared, assuranceClass: assurance, error: null };
}

function bestSet(counted, rules) {
  const ordered = counted.filter((t) => t.genTimeMs !== null && t.parent !== null).sort((a, b) => a.genTimeMs - b.genTimeMs || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  for (let start = 0; start < ordered.length; start++) {
    for (let end = start; end < ordered.length; end++) {
      if (ordered[end].genTimeMs - ordered[start].genTimeMs > rules.maxSpreadS * 1000) break;
      const subset = ordered.slice(start, end + 1);
      if (independent(subset) >= rules.requiredIndependent) return subset;
    }
  }
  return [];
}
