# Security model

Scope: security architecture beyond the trust layer. Status: DESIGNED; of its parts only the secret scan and the verifier (with its attack suite) are built. Product rules: contract sections 16, 32.1a, 35.9, 36, 37, 42, 53; red-team matrix; description 5.6. Companion: `VERIFIER_THREAT_MODEL.md` (ledger and verifier), `PRIVACY_DATA_FLOWS.md`. Sources read: OWASP ASVS 5.0 (cloned; chapter structure and every level-1 requirement of chapters 1, 3, 13, 14 and 15 read; 70 level-1 requirements in the 17 chapters by count), OWASP SSRF Prevention cheat sheet (read earlier for the gateway), AgentDojo (MIT; read, see contract 53), OWASP LLM Top 10 2025 (category names only). **Not read:** the OWASP Top 10 for Agentic Applications 2026 (the page and the PDF links returned only the landing text and an HTML shell; recorded as NOT READ, and its categories are not cited here).

## 1. Trust zones

| Zone | Contents | Trust |
|---|---|---|
| Z0 public internet | visitors, third-party sources, model providers, time-stamp authorities | none |
| Z1 edge | CDN, TLS, rate limits, static pages | stateless; sees no secrets |
| Z2 application | API, view-model renderer, gateway client | holds session identity, no long-lived secrets except the gateway's keys via the secret store |
| Z3 gateway | the only network actor toward RYO, exchanges, model providers, news, time-stamp authorities | holds provider keys; enforces menu and domain allow-list |
| Z4 ledger writer | single writer, log key, outbox | holds the log key; accepts only validated, canonical records from Z2 and the scheduler; **no network egress**: attestation requests go to Z3 through the outbox |
| Z5 data | SQLite, segments, raw payload store, backups | local disk; encrypted backups |
| Z6 user machine | Local Connector, browser key | never trusted by the server beyond what it signs |

Rules: Z2 cannot reach providers except through Z3; no process other than Z4 can append to the ledger; the model (an external service reached only by Z3) returns typed documents and has no credentials, no tools and no network; Z6 secrets (X cookies, tokens) never reach Z0 to Z5.

## 2. Secrets and keys (disqualifier-critical)

- No real key in the repository, ever; `.env.example` only; a pre-commit and CI secret scan (patterns for RYO, model, Tavily and generic high-entropy tokens) blocks commits; `python tools/repo_audit.py` style audit as a CI gate. Prompts contain no secret (prompt-capture test).
- Provider keys live in the host's secret store, read at process start by Z3 only; log redaction removes `Authorization` and any value matching a key pattern; error messages never echo a header.
- The log signing key: encrypted at rest, in memory in Z4 only, rotation by the two-signature rule (ledger spec section 5).
- A key leaked into a log is a security incident with a runbook (rotate, scrub, notify the key issuer, as the RYO guide says to report an exposed credential).

## 3. Identity and abuse

Browser-key identities start low-trust (contract 16); rate limits per key and per address; the investigation budget (35.9); contribution adjudication without votes (32.1a); identity assurance named in three levels and never conflated. Authentication to use a browser key involves no cognitive-function test (WCAG 3.3.8). Session handling follows ASVS chapter 7 level 1 (reviewed when the session design exists); authorization is deny-by-default with object-level checks on every private item (ASVS chapter 8 level 1): a private item is readable only by its owner's identity, tested with sentinel strings (contract 36.7).

## 4. Untrusted content

The channels are those of contract 53.1; the defences by architecture are 53.4. Additional rules from this analysis: (a) every fetched page is size-capped, content-type checked, decoded in a sandboxed parser, and stripped to text before any model sees it; (b) no model output is ever evaluated, rendered as HTML or used to build a URL without the product's link component (ASVS 1.2 and 3.2: output encoding in context, safe rendering of text); (c) structured model output is validated against a schema and rejected, not repaired silently; (d) code written by a model is never run on any host (project rule).

## 5. Network and request safety (the gateway)

Domain allow-list per connector; resolve-then-connect with the resolved address checked against private, loopback, link-local and metadata ranges and re-checked on redirect (the SSRF cheat-sheet guidance, ASVS 15 and 13 families); redirect limit; response size and time limits; one retry layer; DNS pinned for the connection. User-submitted URLs are fetched by the safe client with these rules, never by the model.

## 6. Web frontend

Content Security Policy without inline script (the policy is `deploy/security_headers.json`; the page test serves every page under it in three browsers, and the page document has no inline script or style); HSTS; cookies with `Secure`, `HttpOnly` and `SameSite` and the `__Host-` prefix where used (ASVS 3.3.1); CORS fixed values, not reflected origins (3.4.2); state-changing operations only by non-GET methods (3.5.3); sensitive data never in URLs (14.2.1); no `.git` or build metadata deployed (13.4.1). The RYO key is never in browser code (the guide's rule).

## 7. Supply chain and dependencies (ASVS 15, OpenSSF Scorecard, SLSA, SBOM)

Lockfiles with hashes; a dependency-review step on pull requests; an SBOM (CycloneDX) generated per release; remediation time frames to be written (ASVS 15.1.1 requires documented time frames); only MIT, Apache or BSD runtime dependencies, disclosed in the README; third-party code is built and run in separate directories with `env -i` during evaluation; Node and Python are pinned (CF-12). OpenSSF Scorecard checks are run against our own repository as a measurable score, not a claim.

## 8. Logging and incident response

Structured logs with trace ids, no personal data and no secrets; security events (rejected records, denied access, injection events, budget exhaustion) are separate and alertable; ledger and verifier events are append-only; a runbook per class: key exposure, ledger inconsistency, source poisoning, abuse flood, model-provider outage, time-stamp authority compromise.

## 9. ASVS level-1 coverage plan

ASVS 5.0 level 1 is the floor for every chapter that applies (1 encoding, 2 validation, 3 frontend, 4 API, 5 file handling if uploads exist, 6 and 7 and 8 authentication, sessions, authorization as designed, 9 and 10 only if tokens or OAuth are used, 11 cryptography, 12 communication, 13 configuration, 14 data protection, 15 coding and architecture, 16 logging). The mapping of each applicable level-1 requirement to a test is kept with the tests; a requirement we do not meet is recorded in the exceptions register, never omitted. **Not verified:** that the design meets any ASVS level; no review has been done.

## 10. Failure modes

| Failure | Behaviour |
|---|---|
| A secret reaches a log | secret scan in CI on log fixtures; runbook |
| An injected instruction changes an outcome | a defect against the suite; ASR zero on blocked goals |
| A private item is exposed | sentinel test; incident |
| A provider returns hostile content | size and type caps; text stripping |
| The gateway is bypassed | network policy denies all egress from Z2 and Z4 (and every non-gateway process) except to Z3; a test attempts a connection from each |
| A bundle supplies its own trust anchors | the verifier takes a trusted root from outside the bundle (verification spec section 2); E2c substitution and offline-forgery cases | tested |


## Rules from an earlier study (reproduced in the standard library)

The safe client canonicalises an address with the resolver's own rules before judging it: `127.1`, `2130706433`, `0x7f.0.0.1` and `0177.0.0.1` all mean loopback although `ipaddress.ip_address` rejects them (a check that treats a parse error as a hostname lets them through). It resolves the name, rejects every resulting address that is not globally routable (including 100.64.0.0/10, link-local such as 169.254.169.254 and IPv4-mapped IPv6 after unwrapping), and connects to the checked address, not the name. Mutants: the four numeric forms.
