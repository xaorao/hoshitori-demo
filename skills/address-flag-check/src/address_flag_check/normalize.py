"""Address handling (D3 section 6): accept one mainnet address for one chain, give its matching key and display form, or refuse it with a
field-specific message before any network call. A refusal never repeats the input, so a pasted key or recovery phrase is not echoed.

Also classifies a string as OFAC published it, by its own format and never by the asset label OFAC filed it under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .encoding import base58check_decode, eip55, segwit_decode
from .errors import InputError

CHAINS = ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin")
EVM_CHAINS = CHAINS[:6]
MAX_LENGTH = 100
STRIPPED_NOTE = "leading or trailing spaces were removed from the address"

_EVM = re.compile(r"0x[0-9a-fA-F]{40}")
_EVM_ANY_PREFIX = re.compile(r"0[xX][0-9a-fA-F]{40}")
_HEX32 = re.compile(r"(0[xX])?[0-9a-fA-F]{64}")
_TRON_HEX = re.compile(r"(0x)?41[0-9a-fA-F]{40}")
_WORD = re.compile(r"[a-z]{3,8}")
_PHRASE_LENGTHS = (12, 15, 18, 21, 24)
_NAME_MARKS = ".:/@"

NOT_EVM = "is not an EVM address (0x followed by 40 hex characters)"
NOT_TRON = "is not a Tron address (base58check, version byte 0x41, starts with T)"
NOT_BITCOIN = "is not a Bitcoin mainnet address (starts with 1, 3 or bc1)"


@dataclass(frozen=True)
class Address:
    normalized: str  # the matching key: lower case for EVM and bech32, exactly as given for base58
    display: str  # EIP-55 for EVM, lower case for bech32, as given for base58
    chain: str
    format: str
    checksum: str
    notes: tuple[str, ...]


def _refuse(message: str) -> InputError:
    return InputError("address", message)


def parse_address(value: object, chain: str) -> Address:
    """The normal form of ``value`` as an address on ``chain`` (one of CHAINS), or InputError with the exact D3 wording."""
    if isinstance(value, list):
        raise _refuse("send one address per call; lists are not accepted")
    if not isinstance(value, str):
        raise _refuse("must be a string")
    text = value.strip(" ")
    notes = (STRIPPED_NOTE,) if text != value else ()
    if not text:
        raise _refuse("is required")
    if "," in text or ";" in text:
        raise _refuse("send one address per call; lists are not accepted")
    words = text.split()
    if len(words) > 1:
        if len(words) in _PHRASE_LENGTHS and all(_WORD.fullmatch(w) for w in words):
            raise _refuse("looks like a recovery phrase; this tool takes addresses only")
        if all(len(w) >= 25 for w in words):
            raise _refuse("send one address per call; lists are not accepted")
        raise _refuse("contains spaces, control or non-ASCII characters")
    if any(not "!" <= ch <= "~" for ch in text):
        raise _refuse("contains spaces, control or non-ASCII characters")
    if len(text) > MAX_LENGTH:
        raise _refuse(f"must be at most {MAX_LENGTH} characters")
    if _HEX32.fullmatch(text):
        raise _refuse("is 32 bytes of hex; transaction hashes and private keys are not accepted")
    if any(mark in text for mark in _NAME_MARKS):
        raise _refuse("names (such as ENS) and URLs are not resolved; pass the address")
    if chain == "tron":
        return _tron(text, notes)
    if chain == "bitcoin":
        return _bitcoin(text, notes)
    return _evm(text, chain, notes)


def _evm(text: str, chain: str, notes: tuple[str, ...]) -> Address:
    if not _EVM.fullmatch(text):
        raise _refuse(NOT_EVM)
    digits = text[2:]
    lower = digits.lower()
    if digits == lower:
        checksum = "none_lowercase"
    elif digits == digits.upper():
        checksum = "none_uppercase"
    elif eip55(lower) == text:
        checksum = "eip55_valid"
    else:
        raise _refuse("mixed-case EVM address fails its EIP-55 checksum; check it for a typing error")
    return Address("0x" + lower, eip55(lower), chain, "evm_hex", checksum, notes)


def _tron(text: str, notes: tuple[str, ...]) -> Address:
    if _TRON_HEX.fullmatch(text):
        raise _refuse("Tron hex form is not accepted; pass the base58 form starting with T")
    if _tron_payload_ok(text):
        return Address(text, text, "tron", "tron_base58check", "base58check_valid", notes)
    raise _refuse(NOT_TRON)


def _tron_payload_ok(text: str) -> bool:
    payload = base58check_decode(text)
    return len(text) == 34 and text.startswith("T") and payload is not None and len(payload) == 21 and payload[0] == 0x41


def _bitcoin(text: str, notes: tuple[str, ...]) -> Address:
    lower = text.lower()
    if lower.startswith(("tb1", "bcrt1")):
        raise _refuse("testnet addresses are not accepted")
    if lower.startswith("bc1"):
        if text not in (lower, text.upper()):
            raise _refuse("bech32 address mixes upper and lower case")
        witness = segwit_decode("bc", text)
        if witness is None:
            raise _refuse(NOT_BITCOIN)
        if witness[0] == 0:
            return Address(lower, lower, "bitcoin", "bitcoin_bech32", "bech32_valid", notes)
        return Address(lower, lower, "bitcoin", "bitcoin_bech32m", "bech32m_valid", notes)
    payload = base58check_decode(text)
    if payload is not None and len(payload) == 21:
        if payload[0] == 0x00:
            return Address(text, text, "bitcoin", "bitcoin_p2pkh", "base58check_valid", notes)
        if payload[0] == 0x05:
            return Address(text, text, "bitcoin", "bitcoin_p2sh", "base58check_valid", notes)
        if payload[0] in (0x6F, 0xC4):
            raise _refuse("testnet addresses are not accepted")
    raise _refuse(NOT_BITCOIN)


def classify_published(text: str) -> tuple[str, str | None, str, bool | None]:
    """(family, format, matching key, format_ok) of a string as OFAC published it; family "other" when it is none of ours."""
    s = text.strip()
    if _EVM_ANY_PREFIX.fullmatch(s):
        digits = s[2:]
        lower = digits.lower()
        ok = s[1] == "x" and (digits in (lower, digits.upper()) or eip55(lower) == "0x" + digits)
        return "evm", "evm_hex", "0x" + lower, ok
    lower = s.lower()
    if lower.startswith("bc1"):
        witness = segwit_decode("bc", s)
        if witness is not None:
            return "bitcoin", "bitcoin_bech32" if witness[0] == 0 else "bitcoin_bech32m", lower, True
    if _tron_payload_ok(s):
        return "tron", "tron_base58check", s, True
    payload = base58check_decode(s)
    if payload is not None and len(payload) == 21 and payload[0] in (0x00, 0x05):
        return "bitcoin", "bitcoin_p2pkh" if payload[0] == 0x00 else "bitcoin_p2sh", s, True
    return "other", None, s, None
