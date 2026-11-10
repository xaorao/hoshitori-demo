# address-flag-check

A read-only research skill for agents. For one blockchain address it answers:

> Does the US Treasury's OFAC sanctions list (the SDN list) name this address, and has the USDT or USDC issuer frozen it?

Each answer comes with its evidence: the OFAC entry, the list's age, or the block at which the issuer's own contract was read. Basic chain
facts come with it. **It never says an address is safe.** Not being on a list is reported as "no flag found", with how many sources were
checked and which could not be used. When nothing could be checked, there is no verdict.

```text
0x0000…dEaD on ethereum: no flag found in 3 of 3 sources checked (OFAC SDN list, USDT freeze, USDC freeze). This is not a safety rating.
0xE950…58FF on ethereum: flagged by 3 of 3 sources that answered (OFAC SDN list, USDT freeze, USDC freeze).
```

Both lines are real output (the second address is on OFAC entity entry 47635). It works on Ethereum, Arbitrum, Base, BNB Smart Chain,
Optimism, Polygon, Tron and Bitcoin. No key, account or sign-up is needed.

## Why it exists

RYO's six research tools analyse tokens by symbol. None of them takes a wallet address; `deep_analysis` states "not wallet analysis". An agent
that is about to pay, receive from, or approve an address has no tool to ask whether that address is sanctioned or frozen. The organisers
name "Scam Wallet Detection" as a skill idea. This skill covers the part that public, keyless and licence-clean sources can answer: sanctions
and issuer freezes. It does **not** cover scam reports. Every scam-report source we found needs a key or an accepted agreement, or is stale
or unlicensed (see "Sources").

## Install and first run (about 5 minutes)

**You need** Python 3.12 or later. Check with `python3 --version`. If it shows an older version, use the `uv` line below, which fetches
Python 3.12 for you (install uv from https://docs.astral.sh/uv/), or install Python from https://www.python.org/downloads/.
**Network:** installing downloads the build tool (setuptools) from the Python package index, and the uv line may download Python itself.
The skill has no run-time dependencies. Every check uses the network; the hosts are listed under "Sources".

Run these commands from the folder that holds this README (`skills/address-flag-check`):

```bash
uv venv --python 3.12 .venv && uv pip install -e .            # or, with Python 3.12+ as python3: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/address-flag-check update-list                       # downloads OFAC's list once (5.7 MB); takes a few seconds
.venv/bin/address-flag-check check --address 0x000000000000000000000000000000000000dEaD --chain ethereum
```

**What success looks like.** `update-list` prints JSON with `"action": "downloaded"`, `"error": null` and `"address_strings"` near 1,058.
`check` prints a JSON envelope whose `summary.headline` reads like the first line above, with `"status": "ok"` and `"verdict":
"no_flag_found"`. The list is kept in `~/.cache/address-flag-check/`; set `ADDRESS_FLAG_CHECK_HOME` or pass `--list-dir` to keep it
elsewhere. Later calls confirm it against OFAC with a one-byte request, at most once an hour.

**Certificates on macOS.** A Python from python.org or uv verifies TLS with `/etc/ssl/cert.pem`, which lacks the root certificate that
OFAC's chain ends in. When a source's certificate cannot be verified on macOS, the skill asks once more with macOS's own root certificates
(read with the system's `security` tool), so `update-list` works without a setup step; it never does this when you have set `SSL_CERT_FILE`.

**If `update-list` still reports `"error": "tls_error"`.** Your Python cannot verify OFAC's certificate with the trust store it uses, and the
retry did not help. Export the system's root certificates and point Python at them, then run `update-list` again:

```bash
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > "$HOME/.address-flag-check-roots.pem"
export SSL_CERT_FILE="$HOME/.address-flag-check-roots.pem"
```

On Linux, update the system's CA bundle (for example the `ca-certificates` package) instead.

## Reading the answer

The envelope has the same ten fields as RYO's builder tools: `schema_version`, `tool`, `status`, `data_mode`, `as_of`, `request`, `data`,
`summary` (`headline`, `key_points`), `availability`, `warnings`. Use `data` for decisions; the summary is generated from it.

| `data.verdict` | Meaning | `status` |
|---|---|---|
| `flagged` | At least one source lists the address or has frozen it. | `ok`, or `partial` if another source could not be used |
| `no_flag_found` | Every source in scope answered from a fresh list or a stated block, and none lists or has frozen it. **Not a safety rating.** | `ok` |
| `incomplete` | No source lists it, but at least one could not be used (unavailable, or its list copy is stale). | `partial` |
| `null` | No source could be checked. No verdict. | `unavailable` |

`data_mode` is earned. It is `live` when every answer was fetched in this call, `mixed` when the OFAC list came from the local copy (the
normal case), and `unknown` for injected or recorded data.

The rules behind the wording, each held by a test:

| Rule | Behaviour |
|---|---|
| Absence is never "safe" | No verdict word, number or sentence means safe, clean, low risk, trusted, verified or approved. The only "safe" is the fixed warning "Not being on a list is not evidence that an address is safe."; the only "safety" is "This is not a safety rating.". |
| Three states per source | `listed` (with the entry or the block), `not_listed` (with the list's issue age, last confirmation and size, or the block read), `unavailable` (with a reason from a closed set). |
| Stale copies do not support absence | A list copy last confirmed longer ago than `max_list_age_hours` (default 24) is marked `stale`. A stale absence makes the verdict `incomplete`. A stale listing still flags, with its age. |
| Nothing unknown becomes "no flag" | With every source down the verdict is `null`, the headline says "No verdict." and lists each reason, and the full attempt list is kept in `data.source_calls`. |
| Chain facts are facts | Code at the address and the count of transactions sent from it (EVM), or transaction and output counts (Bitcoin). They never change the verdict or the status. |
| A label is an asset, not a chain | OFAC files each address under an asset label ("Digital Currency Address - ETH", "- USDT", "- XBT"). A match under another label, or on another chain, carries a warning. A string whose format does not fit its label is warned about too (OFAC files one Tron address under XBT). |
| Who saw the address | `data.sent_to` lists every host that received it. The OFAC check alone never sends it anywhere. |
| No personal names | An OFAC match shows the entry id, party type (`individual`, `entity`, `transport`, `other`), programme tags and label. Names are not read. |
| Upstream text is untrusted | Errors use fixed wording per reason; a test feeds instruction-like text through HTTP and JSON-RPC errors and checks that it never reaches the answer. |
| Refuse before calling out | Malformed input is refused with a field-specific message and a working example before any network call or file access. A pasted recovery phrase or 32-byte key is refused without being repeated. |

Example: the burn address on Ethereum, replayed offline from recorded node answers and the trimmed OFAC extract used by the tests (63
address strings; the real list has 1,058). Because the data were injected, `data_mode` is `unknown` and a warning says so. `as_of` and
`data.source_calls` are left out for space, and the list's issue date is shown as a placeholder.

```json
{
  "status": "ok",
  "data_mode": "unknown",
  "data": {
    "address": {"normalized": "0x000000000000000000000000000000000000dead", "display": "0x000000000000000000000000000000000000dEaD",
                "chain": "ethereum", "format": "evm_hex", "checksum": "eip55_valid"},
    "verdict": "no_flag_found",
    "counts": {"in_scope": 3, "listed": 0, "not_listed": 3, "stale": 0, "unavailable": 0},
    "checks": [
      {"source_id": "ofac_sdn", "kind": "sanctions_list", "state": "not_listed", "stale": false,
       "list": {"name": "OFAC SDN list (advanced XML)", "issued": "<DateOfIssue as published>", "issued_age_days": 4,
                "confirmed_current_age_seconds": 3540, "max_age_hours": 24, "sha256": "85286df87285e903...", "address_strings": 63,
                "copy": "recent"},
       "block": null, "entry": null, "entries": [], "raw_value": null, "reason": null,
       "source_url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP"},
      {"source_id": "usdt_freeze_ethereum", "kind": "issuer_freeze", "state": "not_listed", "stale": false, "list": null,
       "block": {"chain": "ethereum", "number": 26318704, "age_seconds": 13, "pinned": true},
       "entry": null, "entries": [], "raw_value": false, "reason": null, "source_url": "https://ethereum-rpc.publicnode.com"},
      {"source_id": "usdc_freeze_ethereum", "kind": "issuer_freeze", "state": "not_listed", "stale": false, "list": null,
       "block": {"chain": "ethereum", "number": 26318704, "age_seconds": 13, "pinned": true},
       "entry": null, "entries": [], "raw_value": false, "reason": null, "source_url": "https://ethereum-rpc.publicnode.com"}
    ],
    "not_covered": [],
    "chain_facts": {"state": "observed", "chain": "ethereum", "block": 26318704, "code_kind": "no_code", "code_bytes": 0, "delegate": null,
                    "sent_tx_count": 0, "source_url": "https://ethereum-rpc.publicnode.com"},
    "sent_to": ["ethereum-rpc.publicnode.com"]
  },
  "summary": {
    "headline": "0x0000…dEaD on ethereum: no flag found in 3 of 3 sources checked (OFAC SDN list, USDT freeze, USDC freeze). This is not a safety rating.",
    "key_points": [
      "OFAC SDN list issued 4 days ago, confirmed current 59 minutes ago, 63 address strings: this address is not on it.",
      "Tether USDT contract on ethereum, block 26318704: isBlackListed is false. This covers Tether USDT on ethereum only.",
      "Circle USDC contract on ethereum, block 26318704: isBlacklisted is false. This covers Circle USDC on ethereum only.",
      "On ethereum at block 26318704: no code at this address; 0 transactions sent from this address."
    ]
  },
  "availability": {"sanctions_list": "available", "issuer_freeze": "available", "chain_facts": "available"},
  "warnings": [
    "This tool reports published lists and issuer freezes only. It does not analyse transactions, counterparties or behaviour.",
    "Not being on a list is not evidence that an address is safe.",
    "This call sent the address to: ethereum-rpc.publicnode.com.",
    "Answers came from an injected source (recorded or test data), not a live fetch in this call; data_mode is therefore unknown."
  ]
}
```

## Input

| Field | Required | Default | Notes |
|---|---|---|---|
| `address` | yes | | One address, at most 100 characters. EVM: `0x` and 40 hex characters; mixed case must pass its EIP-55 checksum. Tron: base58, starting with `T`. Bitcoin mainnet: starting with `1`, `3` or `bc1`. Spaces around it are removed and noted in `request.notes`. |
| `chain` | yes | | `ethereum`, `arbitrum`, `base`, `bsc`, `optimism`, `polygon`, `tron`, `bitcoin` |
| `checks` | no | every check that covers the chain | Any of `ofac_sdn`, `usdt_freeze`, `usdc_freeze`, `chain_facts`, with at least one of the first three. `["ofac_sdn"]` alone keeps the address on your machine. |
| `max_list_age_hours` | no | `24` | 1 to 168. A list copy last confirmed longer ago than this is stale. |

Coverage: `ofac_sdn` on every chain; `usdt_freeze` on Ethereum and Tron; `usdc_freeze` on Ethereum; `chain_facts` on the EVM chains and
Bitcoin. A check the chain does not have is listed in `data.not_covered` and named in the headline.

Refusals (exact messages; each comes with a working example and never repeats the input): `address: is required` ·
`chain: must be one of [...]` · `address: send one address per call; lists are not accepted` ·
`address: contains spaces, control or non-ASCII characters` · `address: mixed-case EVM address fails its EIP-55 checksum; check it for a
typing error` · `address: is not an EVM address (0x followed by 40 hex characters)` · `address: is 32 bytes of hex; transaction hashes and
private keys are not accepted` · `address: looks like a recovery phrase; this tool takes addresses only` · `address: names (such as ENS)
and URLs are not resolved; pass the address` · `address: is not a Tron address (base58check, version byte 0x41, starts with T)` ·
`address: Tron hex form is not accepted; pass the base58 form starting with T` · `address: is not a Bitcoin mainnet address (starts with
1, 3 or bc1)` · `address: bech32 address mixes upper and lower case` · `address: testnet addresses are not accepted` ·
`checks: <name> does not cover <chain>` · `checks: must include at least one of ofac_sdn, usdt_freeze, usdc_freeze` ·
`max_list_age_hours: must be an integer between 1 and 168`. Four more are this skill's own additions: `address: must be a string`,
`address: must be at most 100 characters`, `checks: must be a non-empty list of distinct names from [...]` and
`arguments: unknown fields [...]; allowed [...]`.

## Ways to call it

```python
from address_flag_check import run, run_sources

envelope = run({"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "chain": "tron"})  # Python; raises InputError on bad input
```

```bash
.venv/bin/address-flag-check check --address 1BitcoinEaterAddressDontSendf59kuE --chain bitcoin   # exit code 2 on invalid input
.venv/bin/address-flag-check check --address <addr> --chain base --checks ofac_sdn --no-refresh   # fully offline
.venv/bin/address-flag-check sources --chain tron       # what an absence result covers; sends nothing
.venv/bin/address-flag-check tool                       # both tool definitions (name, title, description, schemas, annotations)
.venv/bin/address-flag-check mcp                        # MCP over stdio
.venv/bin/address-flag-check serve --port 8766          # REST and MCP over HTTP on 127.0.0.1
curl -s localhost:8766/tools/address_flag_check/call -d '{"address":"0x000000000000000000000000000000000000dEaD","chain":"ethereum"}'
```

An MCP client that starts stdio servers can use, for example:

```json
{"mcpServers": {"address-flag-check": {"command": "/full/path/to/skills/address-flag-check/.venv/bin/address-flag-check", "args": ["mcp"]}}}
```

Two tools are served: `address_flag_check` and `address_flag_sources` ("the sources address_flag_check uses, with each list's issue age,
last confirmation, size and chains"). Both are read-only (`readOnlyHint`, `idempotentHint`, `openWorldHint`; not destructive).

REST uses the shapes of RYO's recorded builder API: `GET /health`; `GET /tools` (a list of definitions); `POST /tools/<name>/call` with a
bare argument object, answered `{"tool", "result", "latency_ms"}`. Failures are `{code, message, details, trace_id}`, for example
`NOT_FOUND` with "Unknown tool 'x'." or `INVALID_ARGUMENTS` with the field and a working example.

MCP is served in both protocol eras on `POST /` and on stdio:

* 2024-11-05 to 2025-11-25: `initialize` (an unknown revision is answered with 2025-11-25), `notifications/initialized`, `ping`,
  `tools/list`, `tools/call`.
* 2026-07-28 (stateless): `server/discover` with the server's identity in `_meta["io.modelcontextprotocol/serverInfo"]`; every request
  carries `_meta["io.modelcontextprotocol/protocolVersion"]`; results carry `resultType`; `tools/list` carries `ttlMs` and `cacheScope`.
  Over HTTP an unsupported revision is HTTP 400 with error -32022 and `{supported, requested}`. The `MCP-Protocol-Version`, `Mcp-Method`
  and (for `tools/call`) `Mcp-Name` headers must be present and agree with the body; otherwise the answer is HTTP 400 with -32020.

These follow the official TypeScript SDK and the draft specification, as read in their source. JSON-RPC batches are refused (HTTP 400). GET
and DELETE on `/` are 405 with `Allow: POST`; notifications are 202. The HTTP server binds to 127.0.0.1 by default. It refuses foreign
browser origins (403), rate limits tool calls per client (429 with `Retry-After`), bounds concurrency (503) and answers oversized bodies
with 413.

## Sources

| Source | What it gives | Chains | Host that receives the address |
|---|---|---|---|
| OFAC SDN list, advanced XML (`SDN_ADVANCED.ZIP`, from OFAC's list service) | the entry, party type, programme tags and asset label of every listed address string | all | none: the list is downloaded and searched on your machine |
| Tether's USDT contract, `isBlackListed` | whether the issuer has frozen the address | Ethereum, Tron | `ethereum-rpc.publicnode.com`; Tron: `tron-rpc.publicnode.com`, then `api.trongrid.io` |
| Circle's USDC contract, `isBlacklisted` | whether the issuer has frozen the address | Ethereum | `ethereum-rpc.publicnode.com` |
| Chain facts | code kind and sent count at the stated block; Bitcoin counts | EVM chains, Bitcoin | the chain's PublicNode host; `mempool.space` |

How each is read: the OFAC download follows exactly one redirect, and only to OFAC's S3 GovCloud host; the signed link is never recorded.
The file is parsed as a stream, and entity declarations are refused. Address types are found by the text "Digital Currency Address -" and
not from a fixed list of assets. On EVM chains the node's chain id is checked first, then every read names one block. A Tron constant call
cannot name a block, so the block shown is the newest one read just before the call, and `"pinned": false` says so. Every reply's shape is
checked; anything else is `bad_response`.

Not used: GoPlus (its API licence agreement requires a key and its acceptance), Chainabuse (a proprietary API that needs a key),
Etherscan labels and lists copied from them (Etherscan's terms forbid reuse), Blockscout (its documentation says keys are required, and no
terms for keyless use were found), the Chainalysis sanctions oracle (deprecated; it answered "not sanctioned" for two current designations),
and stale or unlicensed scam lists. `address-flag-check sources` prints the sources it uses and four of those it does not (GoPlus,
Chainabuse, Etherscan labels, the Chainalysis oracle), each with its terms page and the reason.

## Failure behaviour

| Situation | What happens |
|---|---|
| 429, 5xx, network error, time-out | Full-jitter backoff, `Retry-After` honoured and capped at 5 s, two retries. After a 429 the host is left alone for a cool-down. Other 4xx and unusable replies are not retried. |
| OFAC unreachable, copy present | The copy is used with its last confirmation time; once that is older than `max_list_age_hours` it is stale. |
| OFAC unreachable, no copy | `ofac_sdn` is `unavailable` with the reason; the other sources still answer. |
| A node on the wrong chain | Nothing from it is trusted (`bad_response`), and it never receives the address. |
| PublicNode Tron fails | TronGrid is tried. |
| Certificate not trusted by this machine | `tls_error` (see "Install"). |
| Time budget (25 s per call) spent | The remaining sources are `budget_exhausted`. |
| Everything down | `verdict: null`, `status: "unavailable"`, every attempt listed. |

Reasons: `timeout`, `rate_limited`, `server_error`, `network_error`, `bad_response`, `list_not_loaded`, `cooling_down`, `budget_exhausted`,
`tls_error`, each with fixed wording.

## Tests

```bash
uv venv --python 3.12 .venv && uv pip install -c constraints.txt -e ".[dev]"     # pinned test tools: pytest, jsonschema, ruff, mypy
.venv/bin/python -m pytest -k "not every_change"     # offline, a few seconds; recorded public data as fixtures
.venv/bin/python -m pytest -m live                    # calls OFAC, PublicNode, TronGrid and mempool.space with burn addresses
.venv/bin/python -m pytest -k every_change            # mutation sweeps: every small change of every module must be noticed (minutes)
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy
```

Each module has a checks module (`tests/<module>_checks.py`) whose `failures(mod)` runs against the module or a mutated copy. The sweep
harness is `tests/mutation_sweep.py`. Coverage includes:

* EIP-55, BIP-173 and BIP-350 test vectors, and Keccak-256 against `hashlib`'s SHA3-256 at every block boundary.
* Every address refusal with its exact words.
* The OFAC parser on a trimmed real extract and on a synthetic file.
* The verdict rule over every combination of source states.
* Every sentence written out by hand, and a scan of every producible sentence for words that mean safe.
* Whole answers on recorded node replies for Ethereum, five other EVM chains, Tron and Bitcoin.
* Failure paths, MCP in both eras, and REST against a real server on 127.0.0.1.

Fixtures are real public answers, labelled "recorded by B5A from <url>" (`tests/fixtures/README.md`). They are never presented as live. One
synthetic OFAC file is labelled synthetic. To check the parser on the whole recorded OFAC publication (not bundled), download it and set
`ADDRESS_FLAG_CHECK_FULL_ZIP`: the counts must equal those of a separate ElementTree analysis.

## Limits and what is not verified

* Sanctions and issuer freezes only: no transaction tracing, counterparty or behaviour analysis, and no scam-report coverage. An address
  can be risky and appear on no list.
* OFAC lists about 1,058 address strings, mostly Bitcoin, Tron and EVM. Other assets' strings (Litecoin, Monero, Zcash and more) are kept
  but cannot be queried, because those chains are not supported.
* USDT and USDC freezes are read on Ethereum (and USDT on Tron) only. Their deployments on other chains were not probed.
* Reading the sources' terms is not a legal opinion, and this skill is research context, not legal advice or permission to transact.
  OFAC's list is a US Government work (17 U.S.C. 105); its status outside the US is NOT VERIFIED.
* The keyless rate limits of PublicNode, TronGrid and mempool.space were not measured beyond single calls.
* No EIP-7702 delegated address was probed; that case is tested on a synthetic code value written in the EIP's form.
* Conformance to the organisers' unpublished skill contract is NOT VERIFIED; the skill follows the public builder guide's envelope.

## Licence and disclosures

MIT (see `LICENSE`). No run-time dependency: the Python standard library only. Test tools: pytest, jsonschema, ruff, mypy (all MIT), pinned
in `constraints.txt`. Keccak-256 is written from the Keccak/FIPS 202 specification. The bech32 checksum and conversion follow the
reference code in BIP-173 and BIP-350 (BSD-2-Clause). Finding address types by the "Digital Currency Address -" prefix and the file's
feature-type lookup are adapted from 0xB10C's `ofac-sanctioned-digital-currency-addresses` (MIT). Test vectors come from EIP-55, BIP-173
and BIP-350. Data: OFAC's SDN list (a US Government work); public chain state read through PublicNode, TronGrid and mempool.space.

## Publication

`server.json` in this folder is the skill's [MCP Registry](https://registry.modelcontextprotocol.io) manifest (a PyPI package started with `uvx address-flag-check mcp` over stdio), and `schema/` holds the JSON Schema of each tool's reply (`outputSchema`), regenerated with `python3 scripts/write_skill_schemas.py` at the repository root. Each tool definition also carries MCP `annotations` (read-only, non-destructive, idempotent; `openWorldHint` is true only for tools that call the network). It is prepared, not published: the package is not on PyPI and the manifest has not been submitted. `OWNER` in the manifest name is a placeholder to be replaced with the publishing GitHub account or organisation before publication, together with the marker below.

<!-- mcp-name: io.github.OWNER/address-flag-check -->
