"""Each rule of the trusted-root parser and update client is protected by a scenario that fails when the rule is broken.

One guard has no mutant because it is redundant by design: ``validate=True`` in the base64 decoder (the re-encoding
comparison that follows it rejects every spelling the strict decoder would).
"""

from __future__ import annotations

import pytest

from hoshitori import trusted_root
from tests.mutation import mutant
from tests.root_checks import failures

MUTATIONS = {
    # document shape
    "a non-canonical document is accepted": ("if canonical.dumps(document) != payload:", "if False:"),
    "unknown or missing members are tolerated": ("    if keys != members:", "    if False:"),
    "the document type is not checked": ('    if top["type"] != TYPE:', "    if False:"),
    "the format version is not checked": (
        '    if top["format_version"] != FORMAT_VERSION or type(top["format_version"]) is not int:',
        "    if False:",
    ),
    "a boolean passes as the format version": (
        '    if top["format_version"] != FORMAT_VERSION or type(top["format_version"]) is not int:',
        '    if top["format_version"] != FORMAT_VERSION:',
    ),
    "a boolean passes as an integer": (
        "    if type(value) is not int or not minimum <= value <= maximum:",
        "    if not minimum <= value <= maximum:",
    ),
    "integers are not range checked": (
        "    if type(value) is not int or not minimum <= value <= maximum:",
        "    if type(value) is not int:",
    ),
    "a non-string passes as text": ("    if not isinstance(value, str) or not value or len(value) > limit:", "    if False:"),
    "hex text is not checked": ("    if not isinstance(value, str) or len(value) != length or not set(value) <= HEX:", "    if False:"),
    "lists are not bounded": (
        "    if not isinstance(value, list) or not minimum <= len(value) <= MAX_LIST:",
        "    if not isinstance(value, list):",
    ),
    "base64 need not be canonical": (
        "    if base64.b64encode(raw).decode() != value or (length is not None and len(raw) != length):",
        "    if length is not None and len(raw) != length:",
    ),
    "base64 length is not checked": (
        "    if base64.b64encode(raw).decode() != value or (length is not None and len(raw) != length):",
        "    if base64.b64encode(raw).decode() != value:",
    ),
    "the document size is not limited": ("    if len(payload) > MAX_DOCUMENT_BYTES:", "    if False:"),
    # keys and thresholds
    "a root key may be listed twice": ("        if identifier in keys:", "        if False:"),
    "a threshold of zero is allowed": (
        '"root_threshold", minimum=1, maximum=len(keys))',
        '"root_threshold", minimum=0, maximum=len(keys))',
    ),
    "a threshold above the key count is allowed": ('"root_threshold", minimum=1, maximum=len(keys))', '"root_threshold", minimum=1)'),
    # the log
    "an era key may be named for another log": ("        if key.name != origin:", "        if False:"),
    "the open era may be anywhere": ("    if len(open_eras) != 1 or eras[-1].through_size is not None:", "    if False:"),
    "several open eras are allowed": (
        "    if len(open_eras) != 1 or eras[-1].through_size is not None:",
        "    if eras[-1].through_size is not None:",
    ),
    "a log key may appear twice": ("    if len({era.key for era in eras}) != len(eras):", "    if False:"),
    "an era may close before it opens": (
        "        if era.through_size is not None and era.through_size < era.from_size:",
        "        if False:",
    ),
    "eras may overlap": ("        if era.from_size <= previous_through:", "        if False:"),
    "eras may touch": ("        if era.from_size <= previous_through:", "        if era.from_size < previous_through:"),
    # time-stamp rules
    "a pin may name an unknown operator": ("        if name not in operators:", "        if False:"),
    "a root may be pinned twice": ("    if len({pin.sha256 for pin in pinned}) != len(pinned):", "    if False:"),
    "the close may precede the window": ("minimum=window_after, maximum=7200)", "minimum=0, maximum=7200)"),
    "no independent operator may be required": (
        '"required_independent", minimum=2, maximum=16)',
        '"required_independent", minimum=0, maximum=16)',
    ),
    "no clock uncertainty may be assumed": (
        '"u_constant_s", minimum=1, maximum=3600)',
        '"u_constant_s", minimum=0, maximum=3600)',
    ),
    "witnesses required may exceed the keys": ('"witnesses required", maximum=len(witness_keys))', '"witnesses required")'),
    # era rules
    "a key may sign before its era": ("                if size < era.from_size:", "                if False:"),
    "a retired key may sign beyond its closing size": (
        "                if era.through_size is not None and size > era.through_size:",
        "                if False:",
    ),
    "an unknown key has an era": (
        "        for era in self.eras:\n            if era.key == key:",
        "        for era in self.eras:\n            if True:",
    ),
    # envelopes and counting
    "an envelope may carry extra members": (
        '    if not isinstance(value, Mapping) or set(value) != {"payload_b64", "signatures"}:',
        "    if not isinstance(value, Mapping):",
    ),
    "signatures may be unbounded": ("    if not isinstance(raw, list) or len(raw) > MAX_SIGNATURES:", "    if not isinstance(raw, list):"),
    "a signature may carry extra members": (
        '        if not isinstance(item, Mapping) or set(item) != {"keyid", "sig_b64"}:',
        "        if not isinstance(item, Mapping):",
    ),
    "signatures are counted instead of keys": (
        "    return len(counted)",
        "    return sum(1 for identifier, _ in signatures if identifier in root.root_keys)",
    ),
    "signatures are not verified": (
        "            Ed25519PublicKey.from_public_bytes(public).verify(signature, payload)",
        "            pass",
    ),
    "unknown keys count": ("        if public is None:\n            continue", "        if False:\n            continue"),
    # the update client
    "the old threshold is not required": (
        "        if count_valid(current, signatures, payload) < current.root_threshold:",
        "        if False:",
    ),
    "the new threshold is not required": (
        "        if count_valid(proposed, signatures, payload) < proposed.root_threshold:",
        "        if False:",
    ),
    "the version step is not checked": ("        if proposed.version != wanted:", "        if False:"),
    "the chain length is not bounded": ("        if steps >= max_chain:", "        if False:"),
    "the chain bound is off by one": ("        if steps >= max_chain:", "        if steps > max_chain:"),
    "different documents are not a fork": (
        '        if len({repr(candidate.get("payload_b64")) if isinstance(candidate, Mapping) else "?" for candidate in candidates}) > 1:',
        "        if False:",
    ),
    "only the first channel's signatures count": (
        "        signatures = [signature for _, group in parsed for signature in group]",
        "        signatures = parsed[0][1]",
    ),
    "a malformed envelope is skipped": (
        '        except RootError:\n            return result("REJECTED", MALFORMED)\n        payload = parsed[0][0]',
        '        except RootError:\n            return result("CURRENT", OK)\n        payload = parsed[0][0]',
    ),
    "an unparsable document is skipped": (
        '        except RootError:\n            return result("REJECTED", MALFORMED)\n        if count_valid(current',
        '        except RootError:\n            return result("CURRENT", OK)\n        if count_valid(current',
    ),
    "expiry is not checked": ("    if current.expires <= started:", "    if False:"),
    "expiry at the start time is allowed": ("    if current.expires <= started:", "    if current.expires < started:"),
    "the start time need not have a zone": ("    started = ensure_utc(now)", "    started = now"),
    "rollback is not checked": ("    if current.version < floor_version:", "    if False:"),
    "the single-channel flag is never set": (
        "            single = True\n            break",
        "            single = False\n            break",
    ),
    "a step from one channel is followed": ("        if len(candidates) < 2:", "        if len(candidates) < 1:"),
    "the flag is set for a step two channels serve": (
        "        payload = parsed[0][0]",
        "        single = True\n        payload = parsed[0][0]",
    ),
}


def run(old: str, new: str) -> list[str]:
    return failures(mutant("hoshitori.trusted_root", old, new))


def test_real_module_passes() -> None:
    assert failures(trusted_root) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert run(old, new), f"mutant not detected: {name}"
