# Test fixtures

Recorded fixtures are real answers of public, keyless sources, recorded by B5A with plain HTTP requests that carried no key, no cookie and
no account. They are used to test parsing and whole answers offline. They are labelled recorded wherever they appear and are never presented
as live data. Synthetic files are labelled synthetic in their first lines.

| Path | What it is |
|---|---|
| `rpc/<chain>/*.json` | One recorded request and answer each: the request body, the HTTP status, the kept headers (`date`, `content-type`), the raw answer body and its SHA-256. Each file's `label` reads "recorded by B5A from <url>". Ethereum: chain id, block number, the block at that number, USDT `isBlackListed` and USDC `isBlacklisted` for the burn address `0x…dEaD`, for the WETH contract and for an address on OFAC entity entry 47635, their code and sent counts, and one JSON-RPC error shape. Arbitrum, Base, BSC, Optimism, Polygon: chain id, block, code and sent count of `0x…dEaD`. Tron (PublicNode and TronGrid): the newest block header and USDT `isBlackListed` for the black-hole address and two addresses on OFAC entity entries, and one error shape. Bitcoin (mempool.space): the tip height, two address summaries and the "Invalid Bitcoin address" answer. |
| `ofac/sdn_advanced_extract.xml` | A trimmed extract of OFAC's `SDN_ADVANCED.XML`, recorded by B5A from https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP. Every line is a byte-identical line of the recorded file. It keeps the root, `DateOfIssue`, six reference value sets, and the digital-currency features and sanctions entries of four **entity** entries (30518, 33854, 47635, 55045). Names, other features and every other entry were removed. |
| `ofac/recorded_file.json` | The recorded exchange with OFAC (the 302 to a pre-signed S3 link, whose path and query are not kept; the one-byte Range answer; the full answer's size and SHA-256), how the extract was made, and counts of the whole recorded file made by a separate ElementTree analysis (used by the optional whole-file test). |
| `synthetic/sdn_synthetic.xml` | **Synthetic.** Hand-written in the shape of OFAC's file to reach parser cases the real extract does not hold: an individual, a transport and an "other" party, a repeated string on three entries, a Tron string filed under XBT, a website feature, a 64-character string, entries with no sanctions entry, and the WETH contract filed as if listed (so that the contract warning can be reached end to end; WETH is not on OFAC's list). Its ids and programme tags are made up. |

The whole recorded OFAC file (5.7 MB) is not bundled. To run the whole-file test, download the same publication (its SHA-256 is in
`ofac/recorded_file.json`) and run `ADDRESS_FLAG_CHECK_FULL_ZIP=/path/to/SDN_ADVANCED.ZIP .venv/bin/python -m pytest tests/test_ofac_index.py`.
