# Privacy and data flows

Scope: what leaves our servers, what is stored, retention and rights. Status: DESIGNED. Product rules: contract sections 16 (data rights), 18, 32, 36. Companion: `SECURITY_MODEL.md`. **No legal advice is given or implied; terms of third parties are recorded as read or NOT VERIFIED.**

## 1. Data classes (contract 36.1) and where each lives

| Class | Examples | Storage | Public? |
|---|---|---|---|
| PUBLIC_SOURCE | a fetched page, an exchange response | raw store with hash | derived fields public; raw payload per data-rights rule |
| PRIVATE_USER_ASSERTION | pasted claim, screenshot, a private receipt | private store keyed by the owner identity | never, unless published |
| REDISTRIBUTABLE_EXCERPT | a quoted span up to the pre-registered length with its link | public record | yes |
| DERIVED | claim graphs, statuses, check results | private if the input was private | follows the input |
| LEDGER | canonical records, checkpoints | segments | public records only; private items appear as salted digests |
| OPERATIONAL | logs, traces, budgets, rate-limit counters | operational store | no |

## 2. Flows out of our servers (shown to the person before the first submission and at each submission)

| Recipient | Receives | Never receives |
|---|---|---|
| Model provider | the text needed for the stage (a claim, spans), no keys, no identity | other people's private items, keys |
| News-search provider | extracted terms | verbatim private text |
| RYO | symbols only (its guide) | addresses, wallet queries, anything about the person |
| Exchanges and venues | public market queries | anything about the person |
| Time-stamp authorities and logs | SHA-256 digests | content |
| Witnesses (if joined) | checkpoints | records |
| Browser | the page, its own private items | other people's private items |

The model provider's retention and training terms are a separate fact per provider and are **NOT VERIFIED** until the provider is chosen (ADR-009); the choice must record them. A person who brings their own model key in local mode keeps text on their machine.

## 3. Retention and deletion (contract 36.5)

Private payloads are deletable by their owner at any time; the ledger holds digests only for private items so deletion breaks no chain and a receipt then shows `NOT RECONSTRUCTIBLE`; unpublished private investigations are deleted after a stated period (prior: 90 days) unless kept; private items are excluded from public tables, analytics, evaluation studies and sampling unless the person opts into a named study. Backups follow the same deletion by crypto-shredding: private payloads in backups are encrypted with a per-item key that deletion destroys (design; to be tested in the restore test). Study data from user sessions (consent, recordings for accessibility studies) is stored apart, with consent records.

## 4. Identity and personal data

The product collects no real-world identity by default; a browser key is a pseudonym. A verified account link (a person proving control of a platform account) is stored as the handle and the proof, not credentials. Logs carry no personal data beyond a rotating pseudonymous id and a coarse network prefix for rate limits (prior: /24 or /48, not stored beyond 24 hours). Cookies: strictly necessary only; no third-party analytics scripts; product analytics, if any, are first-party aggregates.

## 5. Third-party data rights (open)

Redistribution of raw RYO payloads depends on the organisers' answer (not given): derived fields public, raw private. Exchange terms differ (public market data endpoints are free to read; redistribution of raw data and use terms were **not read** for each venue). The Binance announcement feed is undocumented (CF-11); its terms are an open question. X content is handled by the Local Connector under the person's own terms and never hosted by us without an authorised route. Hyperliquid, Coinbase and Kraken public APIs were used for fixtures only; the product's data-use review per venue is a pre-launch task recorded in `LICENSE_REGISTER.md`.

## 6. Tests

Sentinel strings in private items are searched for in every public endpoint, feed, index, share card, error and log (contract 36.7); a search query built from private text contains only extracted terms; a model prompt capture contains no key and no other person's item; a deleted payload disappears from the store and from a restored backup; an export of one person's data contains everything of theirs and nothing of anyone else's.

## 7. Failure modes

| Failure | Behaviour |
|---|---|
| A provider outage during a private investigation | queued, not sent elsewhere without the person's consent |
| A publish action pressed by mistake | preview equals payload byte for byte; undo within the retention period for unlisted links; a published ledger record cannot be unpublished and the preview says so |
| A person asks for erasure of a published record | the public ledger entry remains (append-only); the content under it is deleted where the data-rights rule allows, leaving the digest; stated before publishing |
