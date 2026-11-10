"""Checks of ``address_flag_check.encoding``, runnable against a mutant: Keccak-256, EIP-55, base58check, bech32 and bech32m.

Expectations come from published vectors (EIP-55 test cases; BIP-173 and BIP-350 test vectors; the Keccak-256 of the empty string and of two
short texts), from ``hashlib.sha3_256`` (the same permutation with the SHA-3 padding byte, an independent implementation), from the four-byte
selectors that worked on chain in the D3 probes, and from an independent big-integer base58 decoder written in B5A's scratch folder.
"""

from __future__ import annotations

import hashlib
import types

from tests.checks_support import Case

EIP55_VECTORS = (  # EIP-55 "Test Cases": each is its own checksummed form
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
)

BECH32_VALID = (  # BIP-173 "Test vectors": valid Bech32 strings
    "A12UEL5L",
    "a12uel5l",
    "an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs",
    "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw",
    "11qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
    "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w",
    "?1ezyfcl",
)
BECH32M_VALID = (  # BIP-350 "Test vectors for Bech32m": valid Bech32m strings
    "A1LQFN3A",
    "a1lqfn3a",
    "an83characterlonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11sg7hg6",
    "abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx",
    "11llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllludsr8",
    "split1checkupstagehandshakeupstreamerranterredcaperredlc445v",
    "?1v759aa",
)
BECH32_INVALID = (  # BIP-173 invalid Bech32 strings
    "\x201nwldj5",
    "\x7f1axkwrx",
    "\x801eym55h",
    "an84characterslonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1569pvx",
    "pzry9x0s0muk",
    "1pzry9x0s0muk",
    "x1b4n0q5v",
    "li1dgmt3",
    "de1lg7wt\xff",
    "A1G7SGD8",
    "10a06t8",
    "1qzzfhee",
)
BECH32M_INVALID = (  # BIP-350 invalid Bech32m strings
    "\x201xj0phk",
    "\x7f1g6xzxy",
    "\x801vctc34",
    "an84characterslonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11d6pts4",
    "qyrz8wqd2c9m",
    "1qyrz8wqd2c9m",
    "y1b0jsk6g",
    "lt1igcx5c0",
    "in1muywd",
    "mm1crxm3i",
    "au1s5cgom",
    "M1VUXWEZ",
    "16plkw9",
    "1p2gdwpf",
)
SEGWIT_VALID = (  # BIP-350 "v0-v16 native segregated witness addresses": address and scriptPubKey
    ("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "0014751e76e8199196d454941c45d1b3a323f1433bd6"),
    (
        "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7",
        "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262",
    ),
    (
        "bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y",
        "5128751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6",
    ),
    ("BC1SW50QGDZ25J", "6002751e"),
    ("bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", "5210751e76e8199196d454941c45d1b3a323"),
    (
        "tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy",
        "0020000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433",
    ),
    (
        "tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c",
        "5120000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433",
    ),
    (
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
        "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    ),
)
SEGWIT_INVALID = (  # BIP-350 invalid segwit addresses (the reason is in the BIP)
    "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
    "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf",
    "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh",
    "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",
    "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4",
    "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R",
    "bc1pw5dgrnzv",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav",
    "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j",
    "bc1gmk9yu",
)


def _script(version: int, program: bytes) -> str:
    return (bytes([version + 0x50 if version else 0, len(program)]) + program).hex()


def check_keccak(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    k = mod.keccak256
    c.eq("keccak256 of nothing", k(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
    c.eq("keccak256 of abc", k(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
    c.eq(
        "keccak256 of the fox",
        k(b"The quick brown fox jumps over the lazy dog").hex(),
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
    )
    for text, selector in (
        ("isBlackListed(address)", "e47d6060"),
        ("isBlacklisted(address)", "fe575a87"),
        ("isSanctioned(address)", "df592f7d"),
        ("transfer(address,uint256)", "a9059cbb"),
        ("balanceOf(address)", "70a08231"),
    ):
        c.eq(f"selector of {text}", k(text.encode())[:4].hex(), selector)
    # The same sponge with the SHA-3 domain byte must equal hashlib's SHA3-256 at every block boundary (rate 136 bytes).
    for n in (0, 1, 55, 135, 136, 137, 271, 272, 273, 500):
        data = bytes((i * 7 + 3) % 256 for i in range(n))
        c.eq(f"sponge with SHA-3 padding, {n} bytes", mod.sponge256(data, 0x06), hashlib.sha3_256(data).digest())
    c.eq("keccak256 returns 32 bytes", len(k(b"x" * 1000)), 32)
    return c.bad


def check_eip55(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for vector in EIP55_VECTORS:
        c.eq(f"EIP-55 of {vector}", mod.eip55(vector[2:].lower()), vector)
    c.eq("EIP-55 of the burn address", mod.eip55("000000000000000000000000000000000000dead"), "0x000000000000000000000000000000000000dEaD")
    c.eq("EIP-55 of WETH", mod.eip55("c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    c.eq(
        "EIP-55 of an address with no letters",
        mod.eip55("0000000000000000000000000000000000000000"),
        "0x0000000000000000000000000000000000000000",
    )
    return c.bad


def check_base58(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    d = mod.base58check_decode
    for text, payload in (  # values from B5A's independent big-integer decoder (scratch expect/b58_independent.txt)
        ("1BitcoinEaterAddressDontSendf59kuE", "00759d6677091e973b9e9d99f19c68fbf43e3f05f9"),
        ("3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8", "05d29be4566f77cf478895f0dc3e1d96692d6ca390"),
        ("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "410000000000000000000000000000000000000000"),
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "41a614f803b6fd780986a42c78ec9c7f77e6ded13c"),
        ("1111111111111111111114oLvT2", "00" + "00" * 20),
        ("mfWxJ45yp2SFn7UciZyNpvDKrzbhyfKrY8", "6f" + "00" * 20),
        ("2MsFDzHRUAMpjHxKyoEHU3aMCMsVtMqs1PV", "c4" + "00" * 20),
        ("5HpHagT65TZzG1PH3CSu63k8DbpvD8s5ip4nEB3kEsreAbuatmU", "80" + "00" * 32),
        # the shortest base58check: a one-byte payload and its four-byte checksum (B5A-2's own encoder, scratch vectors_encoding.txt)
        ("1Wh4bh", "00"),
        ("8M5JoSg", "41"),
    ):
        got = d(text)
        c.eq(f"base58check {text}", None if got is None else got.hex(), payload)
    for label, text in (
        ("a changed last character", "1BitcoinEaterAddressDontSendf59kuF"),
        ("a zero, which base58 leaves out", "1BitcoinEaterAddressDontSendf59ku0"),
        ("a capital O", "1BitcoinEaterAddressDontSendf59kuO"),
        ("a capital I", "1BitcoinEaterAddressDontSendf59kuI"),
        ("a lower-case l", "1BitcoinEaterAddressDontSendf59kul"),
        ("the empty string", ""),
        ("too short for a checksum", "1111"),
        ("a case-folded Tron address", "t9yd14nj9j7xab4dbgeix9h8unkkhxuwwb"),
        ("a non-ASCII digit", "1BitcoinEaterAddressDontSendf59ku\u0661"),
    ):
        c.eq(f"base58check refuses {label}", d(text), None)
    c.eq("base58 of leading ones keeps the zero bytes", mod.b58decode("111z"), b"\x00\x00\x00\x39")
    c.eq("base58 of one character", mod.b58decode("2"), b"\x01")
    c.eq("base58 of the empty string", mod.b58decode(""), b"")
    c.eq("base58 refuses a bad character", mod.b58decode("0"), None)
    c.eq("base58 refuses a tilde, which is outside its 58 characters", mod.b58decode("2~"), None)
    return c.bad


def check_bech32(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for text in BECH32_VALID:
        got = mod.bech32_decode(text)
        c.eq(f"valid bech32 {text!r}", None if got is None else got[2], "bech32")
    for text in BECH32M_VALID:
        got = mod.bech32_decode(text)
        c.eq(f"valid bech32m {text!r}", None if got is None else got[2], "bech32m")
    for text in (*BECH32_INVALID, *BECH32M_INVALID):
        c.eq(f"invalid bech32/bech32m {text!r}", mod.bech32_decode(text), None)
    got = mod.bech32_decode("A12UEL5L")
    c.eq("bech32 decode gives the lower-case hrp and no data", None if got is None else (got[0], list(got[1])), ("a", []))
    # BIP-173 allows every character from 33 ('!') to 126 ('~') in the hrp; checksum from the BIP's reference code (scratch vectors_encoding.txt)
    got = mod.bech32_decode("!~1g73f2j")
    c.eq("bech32 with '!' and '~' in the hrp", None if got is None else (got[0], list(got[1]), got[2]), ("!~", [], "bech32"))
    # '~' is not one of the 32 data characters, even when a checksum computed with it read as the value 32 holds
    c.eq("bech32 refuses a tilde in the data part", mod.bech32_decode("a1~744pch"), None)
    for address, script in SEGWIT_VALID:
        hrp = "tb" if address.lower().startswith("tb1") else "bc"
        got = mod.segwit_decode(hrp, address)
        c.eq(f"segwit {address}", None if got is None else _script(got[0], got[1]), script)
    for address in SEGWIT_INVALID:
        hrp = "tb" if address.lower().startswith(("tb1", "tc1")) else "bc"
        c.eq(f"invalid segwit {address}", mod.segwit_decode(hrp, address), None)
        c.eq(f"invalid segwit {address} under the other hrp", mod.segwit_decode("tb" if hrp == "bc" else "bc", address), None)
    c.eq("a mainnet address under the testnet hrp", mod.segwit_decode("tb", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"), None)
    got = mod.segwit_decode("bc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    c.eq("a recorded bech32 address decodes to witness version 0 and 20 bytes", None if got is None else (got[0], len(got[1])), (0, 20))
    c.eq("convertbits of an empty list", mod.convertbits([], 5, 8, False), [])
    c.eq("convertbits 8 to 5 with padding", mod.convertbits([0xFF], 8, 5, True), [31, 28])
    c.eq("convertbits refuses a value out of range", mod.convertbits([32], 5, 8, False), None)
    c.eq("convertbits refuses a value out of range followed by a valid one", mod.convertbits([32, 0], 5, 8, False), None)
    c.eq("convertbits refuses a value out of range when padding", mod.convertbits([256], 8, 5, True), None)
    # BIP-173: an incomplete group at the end must be 4 bits or less; one 5-bit value leaves 5 bits, even when they are zero
    c.eq("convertbits refuses 5 bits of zero padding", mod.convertbits([0], 5, 8, False), None)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_keccak, check_eip55, check_base58, check_bech32):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
