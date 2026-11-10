// What the page uses of verifier-js (plain JavaScript, no types of its own).

declare module "*/verifier-js/src/verify.mjs" {
  export type Verdict = {
    verifier: string;
    format: number;
    root_of_trust: { state: string; version: number | null; sha256: string | null; status: string };
    integrity: {
      ok: boolean;
      self_consistent: boolean;
      one_checkpoint: boolean;
      checkpoint: { origin: string; size: number; root_sha256: string } | null;
      items: Record<string, { id: string | null; type: string | null; signature: string | null; inclusion: boolean | null; error: string | null }>;
      raw: { verified: string[]; missing: string[]; mismatched: string[]; unreferenced: string[] };
      errors: string[];
    };
    timing: {
      level: string;
      reason: string | null;
      assurance_class: string;
      basis: { required_independent: number; window_before_commit_s: number; window_after_commit_s: number; close_after_commit_s: number; max_spread_s: number; u_constant_s: number } | null;
      tokens: { name: string; valid: boolean; reason: string | null; gen_time: string | null; operator: string | null }[];
      public_commit: { claimed: number; authenticated_offline: number; note: string };
    };
    cycle: {
      declared: boolean;
      steps: number;
      batch_size: number | null;
      members_present: number;
      complete: boolean;
      later: { shown: boolean; failures: number; forecasts: Record<string, { awaiting: string; origin: string }> };
    };
    eligibility: { data_modes: string[]; scorable: boolean; reasons: string[] };
    not_proven: string[];
  };
  export function verifyBundle(directory: string, root: unknown, rootStatus?: string): Verdict;
}

declare module "*/verifier-js/src/trusted_root.mjs" {
  export function parseRoot(bytes: Buffer): unknown;
}
