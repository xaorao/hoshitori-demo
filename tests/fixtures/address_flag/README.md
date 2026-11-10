# Recorded answers of the address-flag skill

Three envelopes that `skills/address-flag-check` produced itself, offline, from recorded public data: the trimmed extract of OFAC's list and
recorded replies of public nodes (the skill's own test fixtures). They are not live reads: the skill marks them `data_mode: unknown`
because its sources were injected, and one of them (`unavailable.json`) is a synthetic failure of every source.

| File | The address | What the skill said |
|---|---|---|
| `no_flag_found.json` | the burn address `0x…dEaD` on Ethereum | `no_flag_found` by 3 of 3 sources |
| `flagged.json` | an address on OFAC entity entry 47635 | `flagged` by 3 of 3 sources |
| `unavailable.json` | the burn address, every source down | no verdict, `status: unavailable` |

The league's reader (`hoshitori.address_screen`) and the gateway provider (`hoshitori.address_gateway`) are checked against them.
