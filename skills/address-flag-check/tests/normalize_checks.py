"""Checks of ``address_flag_check.normalize``, runnable against a mutant: every acceptance with its normal form and every refusal with its exact
words (D3 section 6), and the classification of a published OFAC string without trusting its label.

Expectations are written by hand from the address rules; base58 payloads come from B5A's independent decoder (scratch ``expect``).
"""

from __future__ import annotations

import dataclasses
import types

from tests.checks_support import Case

DEAD = "0x000000000000000000000000000000000000dEaD"
EATER = "1BitcoinEaterAddressDontSendf59kuE"
BLACKHOLE = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
PHRASE = "abandon ability able about above absent absorb abstract absurd abuse access accident"
# The first 24 words of the BIP-39 English list, for phrases of every length BIP-39 allows (12, 15, 18, 21, 24 words)
BIP39_24 = [
    "abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse", "access", "accident",
    "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act", "action", "actor", "actress", "actual",
]  # fmt: skip
SPACES = "address: contains spaces, control or non-ASCII characters"

NOT_EVM = "address: is not an EVM address (0x followed by 40 hex characters)"
NOT_TRON = "address: is not a Tron address (base58check, version byte 0x41, starts with T)"
NOT_BTC = "address: is not a Bitcoin mainnet address (starts with 1, 3 or bc1)"


def _form(a: object) -> tuple[object, ...]:
    return (a.normalized, a.display, a.chain, a.format, a.checksum, a.notes)  # type: ignore[attr-defined]


def check_accepted(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = mod.parse_address
    for label, value, chain, want in (
        (
            "an EIP-55 address",
            DEAD,
            "ethereum",
            ("0x000000000000000000000000000000000000dead", DEAD, "ethereum", "evm_hex", "eip55_valid", ()),
        ),
        (
            "a lower-case address",
            DEAD.lower(),
            "base",
            ("0x000000000000000000000000000000000000dead", DEAD, "base", "evm_hex", "none_lowercase", ()),
        ),
        (
            "an upper-case address",
            "0x000000000000000000000000000000000000DEAD",
            "polygon",
            ("0x000000000000000000000000000000000000dead", DEAD, "polygon", "evm_hex", "none_uppercase", ()),
        ),
        (
            "an address with no letters",
            "0x" + "0" * 40,
            "bsc",
            ("0x" + "0" * 40, "0x" + "0" * 40, "bsc", "evm_hex", "none_lowercase", ()),
        ),
        (
            "an address with spaces around it",
            "  " + DEAD + " ",
            "arbitrum",
            (
                "0x000000000000000000000000000000000000dead",
                DEAD,
                "arbitrum",
                "evm_hex",
                "eip55_valid",
                ("leading or trailing spaces were removed from the address",),
            ),
        ),
        ("a Tron address", BLACKHOLE, "tron", (BLACKHOLE, BLACKHOLE, "tron", "tron_base58check", "base58check_valid", ())),
        ("a P2PKH address", EATER, "bitcoin", (EATER, EATER, "bitcoin", "bitcoin_p2pkh", "base58check_valid", ())),
        (
            "a P2SH address",
            "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8",
            "bitcoin",
            (
                "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8",
                "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8",
                "bitcoin",
                "bitcoin_p2sh",
                "base58check_valid",
                (),
            ),
        ),
        ("a bech32 address", BECH32, "bitcoin", (BECH32, BECH32, "bitcoin", "bitcoin_bech32", "bech32_valid", ())),
        ("an upper-case bech32 address", BECH32.upper(), "bitcoin", (BECH32, BECH32, "bitcoin", "bitcoin_bech32", "bech32_valid", ())),
        ("a bech32m address", TAPROOT, "bitcoin", (TAPROOT, TAPROOT, "bitcoin", "bitcoin_bech32m", "bech32m_valid", ())),
    ):
        got = c.run(label, lambda value=value, chain=chain: p(value, chain))
        c.eq(label, None if got is None else _form(got), want)
    c.eq("chains", mod.CHAINS, ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin"))
    c.eq("EVM chains", mod.EVM_CHAINS, ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon"))
    got = c.run("the dead address for the frozen test", lambda: p(DEAD, "ethereum"))
    if got is not None:
        c.raises("an Address cannot be changed after it is made", lambda: setattr(got, "chain", "tron"), dataclasses.FrozenInstanceError)
        c.eq("an Address can be a dict key", c.run("hashing an Address", lambda: {got: 1}.get(got)), 1)
    return c.bad


def check_refused(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = mod.parse_address
    hash32 = "ab" * 32
    for label, value, chain, says in (
        ("the empty string", "", "ethereum", "address: is required"),
        ("only spaces", "   ", "ethereum", "address: is required"),
        ("a number", 12345, "ethereum", "address: must be a string"),
        ("a list", [DEAD, DEAD], "ethereum", "address: send one address per call; lists are not accepted"),
        ("two addresses with a comma", f"{DEAD},{DEAD}", "ethereum", "address: send one address per call; lists are not accepted"),
        ("two addresses with a semicolon", f"{DEAD};{DEAD}", "ethereum", "address: send one address per call; lists are not accepted"),
        ("two addresses on two lines", f"{DEAD}\n{DEAD}", "ethereum", "address: send one address per call; lists are not accepted"),
        (
            "an inner space",
            "0x0000000000000000000 00000000000000000dEaD",
            "ethereum",
            "address: contains spaces, control or non-ASCII characters",
        ),
        ("a zero-width space", DEAD + "\u200b", "ethereum", "address: contains spaces, control or non-ASCII characters"),
        (
            "a Cyrillic look-alike",
            "0x000000000000000000000000000000000000d\u0415aD",
            "ethereum",
            "address: contains spaces, control or non-ASCII characters",
        ),
        ("a control character", DEAD + "\x07", "ethereum", "address: contains spaces, control or non-ASCII characters"),
        (
            "a tab inside",
            "0x00000000000000000000\t000000000000000000dEaD",
            "ethereum",
            "address: contains spaces, control or non-ASCII characters",
        ),
        ("a recovery phrase", PHRASE, "ethereum", "address: looks like a recovery phrase; this tool takes addresses only"),
        (
            "a 24-word recovery phrase",
            " ".join([*PHRASE.split(), *PHRASE.split()]),
            "bitcoin",
            "address: looks like a recovery phrase; this tool takes addresses only",
        ),
        ("101 characters", "a" * 101, "ethereum", "address: must be at most 100 characters"),
        (
            "32 bytes of hex with 0x",
            "0x" + hash32,
            "ethereum",
            "address: is 32 bytes of hex; transaction hashes and private keys are not accepted",
        ),
        ("32 bytes of hex without 0x", hash32, "tron", "address: is 32 bytes of hex; transaction hashes and private keys are not accepted"),
        ("an ENS name", "vitalik.eth", "ethereum", "address: names (such as ENS) and URLs are not resolved; pass the address"),
        (
            "a URL",
            "https://example.org/address/" + DEAD[:30],
            "ethereum",
            "address: names (such as ENS) and URLs are not resolved; pass the address",
        ),
        ("an e-mail style name", "someone@example", "bitcoin", "address: names (such as ENS) and URLs are not resolved; pass the address"),
        ("a URI with a scheme", "bitcoin:" + EATER, "bitcoin", "address: names (such as ENS) and URLs are not resolved; pass the address"),
        (
            "mixed case failing EIP-55",
            "0x000000000000000000000000000000000000DeAD",
            "ethereum",
            "address: mixed-case EVM address fails its EIP-55 checksum; check it for a typing error",
        ),
        ("a capital 0X prefix", "0X000000000000000000000000000000000000dEaD", "ethereum", NOT_EVM),
        ("no 0x prefix", DEAD[2:], "ethereum", NOT_EVM),
        ("39 hex characters", DEAD[:-1], "ethereum", NOT_EVM),
        ("41 hex characters", DEAD + "0", "ethereum", NOT_EVM),
        ("a non-hex character", DEAD[:-1] + "g", "ethereum", NOT_EVM),
        ("a Tron address on ethereum", BLACKHOLE, "ethereum", NOT_EVM),
        ("a Bitcoin address on optimism", EATER, "optimism", NOT_EVM),
        ("an EVM address on tron", DEAD, "tron", NOT_TRON),
        ("a Tron hex form", "41" + "00" * 20, "tron", "address: Tron hex form is not accepted; pass the base58 form starting with T"),
        (
            "a Tron hex form with 0x",
            "0x41" + "ab" * 20,
            "tron",
            "address: Tron hex form is not accepted; pass the base58 form starting with T",
        ),
        ("a Tron address with a bad checksum", BLACKHOLE[:-1] + "c", "tron", NOT_TRON),
        ("a case-folded Tron address", BLACKHOLE.lower(), "tron", NOT_TRON),
        ("a Bitcoin address on tron", EATER, "tron", NOT_TRON),
        ("a base58check string of the Tron version that is too long", "TBXSw8fM4jpQkGc6zZjsVABFpVN7UvXPdVV", "tron", NOT_TRON),
        ("an EVM address on bitcoin", DEAD, "bitcoin", NOT_BTC),
        ("a Tron address on bitcoin", BLACKHOLE, "bitcoin", NOT_BTC),
        ("a P2PKH address with a bad checksum", EATER[:-1] + "F", "bitcoin", NOT_BTC),
        ("a Litecoin address", "LKDxGDJq5fF4FohAB8zJH24mDDNHDNtqsE", "bitcoin", NOT_BTC),
        ("a private key in wallet import format", "5HpHagT65TZzG1PH3CSu63k8DbpvD8s5ip4nEB3kEsreAbuatmU", "bitcoin", NOT_BTC),
        ("a bech32 address with a bad checksum", BECH32[:-1] + "a", "bitcoin", NOT_BTC),
        ("a bech32 address under bech32m rules", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh", "bitcoin", NOT_BTC),
        (
            "a bech32 address mixing case",
            "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdQ",
            "bitcoin",
            "address: bech32 address mixes upper and lower case",
        ),
        (
            "a testnet bech32 address",
            "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7",
            "bitcoin",
            "address: testnet addresses are not accepted",
        ),
        (
            "a testnet bech32 address in capitals",
            "TB1QRP33G0Q5C5TXSP9ARYSRX4K6ZDKFS4NCE4XJ0GDCCCEFVPYSXF3Q0SL5K7",
            "bitcoin",
            "address: testnet addresses are not accepted",
        ),
        (
            "a regtest bech32 address",
            "bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080",
            "bitcoin",
            "address: testnet addresses are not accepted",
        ),
        ("a testnet P2PKH address", "mfWxJ45yp2SFn7UciZyNpvDKrzbhyfKrY8", "bitcoin", "address: testnet addresses are not accepted"),
        ("a testnet P2SH address", "2MsFDzHRUAMpjHxKyoEHU3aMCMsVtMqs1PV", "bitcoin", "address: testnet addresses are not accepted"),
        # every BIP-39 length is a recovery phrase; two words, or twelve words that are not all lower-case words, are not
        *(
            (
                f"a recovery phrase of {n} words",
                " ".join(BIP39_24[:n]),
                "ethereum",
                "address: looks like a recovery phrase; this tool takes addresses only",
            )
            for n in (15, 18, 21)
        ),
        ("two lower-case words", "abandon ability", "ethereum", SPACES),
        ("twelve words, one capitalised", "Abandon " + " ".join(BIP39_24[1:12]), "ethereum", SPACES),
        # two words of 25 or more characters each read as a list; a long word beside a short one does not
        ("two 25-character words", "a" * 25 + " " + "b" * 25, "ethereum", "address: send one address per call; lists are not accepted"),
        ("an address and a short word", DEAD + " abc", "ethereum", SPACES),
        # exactly 100 characters is within the limit, so the address rules decide
        ("100 characters", "a" * 100, "ethereum", NOT_EVM),
        # '!' and '~' are the ends of the printable range: they pass the character test and then fail the address rules
        ("an exclamation mark in place of a digit", DEAD[:-1] + "!", "ethereum", NOT_EVM),
        ("a tilde before the address", "~" + DEAD, "ethereum", NOT_EVM),
    ):
        c.raises(label, lambda value=value, chain=chain: p(value, chain), mod.InputError, says)
    exc = c.raises("a recovery phrase is not echoed", lambda: p(PHRASE, "ethereum"), mod.InputError)
    if exc is not None:
        c.true("the refusal does not carry the phrase", "abandon" not in repr(exc.__dict__) and "abandon" not in str(exc))
    exc = c.raises("a 32-byte value is not echoed", lambda: p("0x" + hash32, "ethereum"), mod.InputError)
    if exc is not None:
        c.true("the refusal does not carry the value", hash32 not in repr(exc.__dict__) and hash32 not in str(exc))
    return c.bad


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    k = mod.classify_published
    for label, text, want in (
        (
            "a checksummed EVM string",
            "0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF",
            ("evm", "evm_hex", "0xe950dc316b836e4eefb8308bf32bf7c72a1358ff", True),
        ),
        (
            "a lower-case EVM string",
            "0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a",
            ("evm", "evm_hex", "0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a", True),
        ),
        (
            "a mixed-case EVM string failing EIP-55",
            "0x000000000000000000000000000000000000DeAD",
            ("evm", "evm_hex", "0x000000000000000000000000000000000000dead", False),
        ),
        (
            "an EVM string with a capital 0X",
            "0X000000000000000000000000000000000000dEaD",
            ("evm", "evm_hex", "0x000000000000000000000000000000000000dead", False),
        ),
        ("a Tron string", "TL1k1U6SHohxBqb68kCodxHc9y2LXoDSep", ("tron", "tron_base58check", "TL1k1U6SHohxBqb68kCodxHc9y2LXoDSep", True)),
        ("a P2PKH string", EATER, ("bitcoin", "bitcoin_p2pkh", EATER, True)),
        ("a P2SH string", "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8", ("bitcoin", "bitcoin_p2sh", "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8", True)),
        ("an upper-case bech32 string", BECH32.upper(), ("bitcoin", "bitcoin_bech32", BECH32, True)),
        ("a bech32m string", TAPROOT, ("bitcoin", "bitcoin_bech32m", TAPROOT, True)),
        ("a Litecoin string", "LKDxGDJq5fF4FohAB8zJH24mDDNHDNtqsE", ("other", None, "LKDxGDJq5fF4FohAB8zJH24mDDNHDNtqsE", None)),
        ("a 64-character string", "ab" * 32, ("other", None, "ab" * 32, None)),
        ("a string with spaces around it", " " + EATER + " ", ("bitcoin", "bitcoin_p2pkh", EATER, True)),
    ):
        c.eq(label, k(text), want)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_accepted, check_refused, check_classify):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
