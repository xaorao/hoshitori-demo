"""Scenarios for the trusted-root document parser and the update client (ADR-028)."""

from __future__ import annotations

import base64
import copy
import types
from collections.abc import Callable
from datetime import datetime

from hoshitori import canonical
from hoshitori.note import Signer
from tests import root_support as R
from tests.root_support import make_document, new_signer

Mutation = Callable[[dict[str, object]], None]
KEY1, KEY2, KEY3 = new_signer(1), new_signer(2), new_signer(3)


def document(**kwargs: object) -> dict[str, object]:
    return make_document(**kwargs)  # type: ignore[arg-type]


def parse_rejects(bad: list[str], mod: types.ModuleType, label: str, payload: bytes, why: str) -> None:
    try:
        mod.parse_root(payload)
    except mod.RootError as exc:
        if exc.code != mod.MALFORMED or why not in str(exc):
            bad.append(f"{label}: rejected for another reason ({exc})")
        return
    except Exception as exc:
        bad.append(f"{label}: raised {type(exc).__name__} instead of RootError: {exc}")
        return
    bad.append(f"{label}: accepted")


def mutated(bad: list[str], mod: types.ModuleType, label: str, change: Mutation, why: str, **kwargs: object) -> None:
    base = document(**kwargs)
    candidate = copy.deepcopy(base)
    change(candidate)
    try:
        payload = canonical.dumps(candidate)  # type: ignore[arg-type]
    except canonical.CanonicalError as exc:
        bad.append(f"{label}: the test could not build the document ({exc})")
        return
    parse_rejects(bad, mod, label, payload, why)


def setter(path: list[str], value: object) -> Mutation:
    def apply(doc: dict[str, object]) -> None:
        node: object = doc
        for key in path[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]  # type: ignore[index]
        last = path[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value  # type: ignore[index]

    return apply


def remover(path: list[str]) -> Mutation:
    def apply(doc: dict[str, object]) -> None:
        node: object = doc
        for key in path[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]  # type: ignore[index]
        del node[path[-1]]  # type: ignore[arg-type,attr-defined]

    return apply


# ---------------------------------------------------------------------------------------------------------------------
def parses_valid_documents(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    root = R.initial(3, root_keys=[KEY1, KEY2], threshold=2)
    if root.version != 3 or root.root_threshold != 2 or len(root.root_keys) != 2:
        bad.append("document fields not read")
    if root.origin != R.LOG_ORIGIN or len(root.eras) != 1 or root.eras[0].through_size is not None:
        bad.append("log not read")
    stamp = root.time_stamp
    if (stamp.required_independent, stamp.window_before_commit_s, stamp.window_after_commit_s, stamp.close_after_commit_s) != (
        2,
        1,
        60,
        90,
    ):
        bad.append("time-stamp rules not read")
    if root.expires != datetime.fromisoformat("2027-10-29T00:00:00+00:00"):
        bad.append(f"expiry not read: {root.expires}")
    if mod.key_id(R.public_bytes(KEY1)) not in root.root_keys:
        bad.append("root key ids are not the hash of the key")
    if stamp.pinned[0].der is None or stamp.operators["operator-a"].evidence is None:
        bad.append("pinned certificate or evidence missing")
    # an operator without evidence, a pin without a certificate, witnesses, and several eras are all valid
    second = R.log_signer(8).verifier.vkey()
    two = [
        {"vkey": R.log_signer(7).verifier.vkey(), "from_size": 0, "through_size": 99},
        {"vkey": second, "from_size": 100, "through_size": None},
    ]
    doc = document(eras=two, witnesses=(1, [R.log_signer(9).verifier.vkey()]))
    doc["time_stamp"]["operators"]["operator-a"]["ownership_evidence"] = None  # type: ignore[index]
    doc["time_stamp"]["pinned_roots"][0]["der_b64"] = None  # type: ignore[index]
    try:
        parsed = mod.parse_root(canonical.dumps(doc))  # type: ignore[arg-type]
        if parsed.time_stamp.operators["operator-a"].evidence is not None or parsed.time_stamp.pinned[0].der is not None:
            bad.append("null evidence or certificate not read as absent")
        if len(parsed.eras) != 2 or parsed.witnesses_required != 1 or len(parsed.witness_keys) != 1:
            bad.append("eras or witnesses not read")
    except mod.RootError as exc:
        bad.append(f"valid document with optional parts rejected ({exc})")

    key7, key8 = R.log_signer(7).verifier, R.log_signer(8).verifier
    two_era = mod.parse_root(canonical.dumps(document(eras=two)))  # type: ignore[arg-type]
    checks = [
        (key7, 50, mod.ERA_ACCEPT),
        (key7, 99, mod.ERA_ACCEPT),
        (key7, 100, mod.ERA_BEYOND),
        (key8, 100, mod.ERA_ACCEPT),
        (key8, 99, mod.ERA_BEFORE),
        (key8, 10**9, mod.ERA_ACCEPT),
        (R.log_signer(5).verifier, 1, mod.ERA_UNKNOWN),
    ]
    for key, size, expected in checks:
        got = two_era.era_check(key, size)
        if got != expected:
            bad.append(f"era_check({key.name}, {size}) gave {got}, expected {expected}")
    pins = root.pinned_by_fingerprint()
    if set(pins) != {pin.sha256 for pin in stamp.pinned}:
        bad.append("pinned_by_fingerprint is wrong")
    return bad


def rejects_documents(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    good = canonical.dumps(document())  # type: ignore[arg-type]

    def m(label: str, change: Mutation, why: str, **kwargs: object) -> None:
        mutated(bad, mod, label, change, why, **kwargs)

    # envelope-level shape
    m("unknown top-level member", setter(["extra"], 1), "unexpected")
    m("missing member", remover(["witnesses"]), "missing")
    m("wrong type", setter(["type"], "something-else"), "not a trusted-root")
    m("format version 99", setter(["format_version"], 99), "format_version")
    m("format version true", setter(["format_version"], True), "format_version")
    m("version 0", setter(["version"], 0), "version")
    m("version true", setter(["version"], True), "version")
    m("version as text", setter(["version"], "1"), "version")
    m("expiry without an offset", setter(["expires"], "2027-10-29T00:00:00"), "expires")
    m("expiry not a time", setter(["expires"], "tomorrow"), "expires")
    # keys and thresholds
    m("no root keys", setter(["root_keys"], []), "root_keys")
    m("a root key listed twice", setter(["root_keys"], [base64.b64encode(R.public_bytes(KEY1)).decode()] * 2), "twice", threshold=1)
    m("a short root key", setter(["root_keys"], [base64.b64encode(b"x" * 31).decode()]), "32 bytes")
    m("a root key in non-canonical base64", setter(["root_keys"], [base64.b64encode(R.public_bytes(KEY1)).decode() + "\n"]), "base64")
    m(
        "a root key with padding bits set",
        setter(["root_keys"], [noncanonical(base64.b64encode(R.public_bytes(KEY1)).decode())]),
        "canonical base64",
    )
    m(
        "a root key with a newline in its base64",
        setter(
            ["root_keys"],
            [base64.b64encode(R.public_bytes(KEY1)).decode()[:10] + "\n" + base64.b64encode(R.public_bytes(KEY1)).decode()[10:]],
        ),
        "base64",
    )
    m("threshold 0", setter(["root_threshold"], 0), "root_threshold")
    m("threshold above the number of keys", setter(["root_threshold"], 2), "root_threshold")
    m("threshold true", setter(["root_threshold"], True), "root_threshold")
    m("missing threshold", remover(["root_threshold"]), "missing")
    # log
    m("empty origin", setter(["log", "origin"], ""), "origin")
    m(
        "era key for another log",
        setter(["log", "eras", "0", "vkey"], Signer.from_seed("other.example/log", (7).to_bytes(32, "big")).verifier.vkey()),
        "log origin",
    )
    m("era key that does not parse", setter(["log", "eras", "0", "vkey"], "not a key"), "vkey")
    m("negative era start", setter(["log", "eras", "0", "from_size"], -1), "from_size")
    m("no eras", setter(["log", "eras"], []), "eras")
    one, two = R.log_signer(7).verifier.vkey(), R.log_signer(8).verifier.vkey()
    m(
        "two open eras",
        setter(["log", "eras"], [{"vkey": one, "from_size": 0, "through_size": None}, {"vkey": two, "from_size": 5, "through_size": None}]),
        "exactly one era is open",
    )
    m(
        "open era first",
        setter(["log", "eras"], [{"vkey": one, "from_size": 0, "through_size": None}, {"vkey": two, "from_size": 5, "through_size": 9}]),
        "exactly one era is open",
    )
    m(
        "same key in two eras",
        setter(["log", "eras"], [{"vkey": one, "from_size": 0, "through_size": 4}, {"vkey": one, "from_size": 5, "through_size": None}]),
        "two eras",
    )
    m(
        "an era closes before it opens",
        setter(["log", "eras"], [{"vkey": one, "from_size": 5, "through_size": 4}, {"vkey": two, "from_size": 5, "through_size": None}]),
        "closes before",
    )
    m(
        "overlapping eras",
        setter(["log", "eras"], [{"vkey": one, "from_size": 0, "through_size": 9}, {"vkey": two, "from_size": 9, "through_size": None}]),
        "overlap",
    )
    # time stamps and the registry
    m("pin for an unknown operator", setter(["time_stamp", "pinned_roots", "0", "operator"], "nobody"), "registry")
    m("pin that is not hex", setter(["time_stamp", "pinned_roots", "0", "sha256"], "z" * 64), "sha256")
    m("pin in upper case", setter(["time_stamp", "pinned_roots", "0", "sha256"], "A" * 64), "sha256")
    m("certificate that does not match its pin", setter(["time_stamp", "pinned_roots", "0", "sha256"], "0" * 64), "does not match")
    m("no pinned roots", setter(["time_stamp", "pinned_roots"], []), "pinned_roots")
    m("close earlier than the window", setter(["time_stamp", "close_after_commit_s"], 10), "close_after_commit_s")
    m("no independent operators required", setter(["time_stamp", "required_independent"], 0), "required_independent")
    m("one operator is not independent of itself", setter(["time_stamp", "required_independent"], 1), "required_independent")
    m("no clock uncertainty is not a margin", setter(["time_stamp", "u_constant_s"], 0), "u_constant_s")
    m("a negative clock uncertainty", setter(["time_stamp", "u_constant_s"], -1), "u_constant_s")
    m("a clock uncertainty beyond an hour", setter(["time_stamp", "u_constant_s"], 3601), "u_constant_s")
    m("a negative window", setter(["time_stamp", "window_before_commit_s"], -1), "window_before_commit_s")
    m("evidence missing a field", remover(["time_stamp", "operators", "operator-a", "ownership_evidence", "ref"]), "missing")
    m(
        "evidence subject not hex",
        setter(["time_stamp", "operators", "operator-a", "ownership_evidence", "subject_sha256"], "x"),
        "subject_sha256",
    )
    m("an operator with an empty parent", setter(["time_stamp", "operators", "operator-a", "parent"], ""), "parent")
    m("no operators", setter(["time_stamp", "operators"], {}), "operators")
    m("witnesses required above the keys", setter(["witnesses", "required"], 1), "witnesses required")
    m("empty data mode", setter(["scored_requires_data_mode"], ""), "scored_requires_data_mode")

    # the same pin twice
    def pin_twice(doc: dict[str, object]) -> None:
        pins = doc["time_stamp"]["pinned_roots"]  # type: ignore[index]
        pins.append(copy.deepcopy(pins[0]))

    m("a root pinned twice", pin_twice, "pinned twice")

    # bytes that are not a canonical document
    parse_rejects(bad, mod, "keys out of canonical order", good.replace(b'"expires"', b'"expires" ', 1), "canonical")
    parse_rejects(bad, mod, "whitespace", good + b"\n", "canonical")
    parse_rejects(bad, mod, "not UTF-8", b"\xff\xfe", "document")
    parse_rejects(bad, mod, "not an object", b"[1]", "document")
    parse_rejects(bad, mod, "empty", b"", "document")
    parse_rejects(bad, mod, "a fractional number", good.replace(b'"version":1', b'"version":1.5', 1), "document")
    parse_rejects(bad, mod, "too large", b"{" + b" " * (mod.MAX_DOCUMENT_BYTES + 1) + b"}", "too large")
    return bad


# ---------------------------------------------------------------------------------------------------------------------
def build_chain(versions: int, **per_version: object) -> list[dict[str, object]]:
    """Documents 1..versions, all keyed by KEY1 with threshold 1, so each step is signed by KEY1."""
    return [document(version=n, **per_version) for n in range(1, versions + 1)]


def channel(documents: list[dict[str, object]], signers: list, start: int = 2) -> dict[int, object]:  # type: ignore[type-arg]
    return {doc["version"]: R.envelope(doc, signers) for doc in documents if doc["version"] >= start}  # type: ignore[misc,operator]


def update_result(mod: types.ModuleType, initial_doc: dict[str, object], channels: list[dict[int, object]], **kwargs: object):  # type: ignore[no-untyped-def]
    """An update as the verifier would run it. A step is followed only when two independent channels serve it (contract 8), so a scenario
    that lists one channel as the place the documents came from gets a second one that serves the same documents; ``alone=True`` gives
    the channels exactly as listed (the scenarios of the two-channel rule itself)."""
    root = mod.parse_root(canonical.dumps(initial_doc))  # type: ignore[arg-type]
    listed = list(channels) if kwargs.pop("alone", False) else [*channels, *(dict(c) for c in channels)]
    return mod.update(root, listed, kwargs.pop("now", R.NOW), **kwargs)  # type: ignore[arg-type]


def expect(bad: list[str], label: str, result: object, status: str, code: str, version: int) -> None:
    if (result.status, result.code, result.root.version) != (status, code, version):  # type: ignore[attr-defined]
        bad.append(f"{label}: got {(result.status, result.code, result.root.version)}, expected {(status, code, version)}")  # type: ignore[attr-defined]


def update_chain(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    docs = build_chain(4)
    first = docs[0]
    both = channel(docs, [KEY1])

    expect(bad, "no newer version", update_result(mod, first, [{}, {}]), "CURRENT", "OK", 1)
    expect(bad, "no channels at all", update_result(mod, first, []), "CURRENT", "OK", 1)
    expect(bad, "one step", update_result(mod, first, [{2: both[2]}]), "CURRENT", "OK", 2)
    expect(bad, "three steps", update_result(mod, first, [both]), "CURRENT", "OK", 4)
    expect(bad, "two channels agree", update_result(mod, first, [both, both]), "CURRENT", "OK", 4)
    # a withheld version ends the chain at the last version served: the documented window, bounded by the root's expiry
    expect(bad, "version 3 withheld", update_result(mod, first, [{2: both[2], 4: both[4]}]), "CURRENT", "OK", 2)

    # the two-channel rule: a step served by one channel is not followed, and the result says so
    one_of_two = update_result(mod, first, [{2: both[2]}, {}], alone=True)
    expect(bad, "a step served by one of two channels is not followed", one_of_two, "CURRENT", "OK", 1)
    if not one_of_two.single_channel:
        bad.append("a step served by one of two channels is not flagged")
    only = update_result(mod, first, [both], alone=True)
    expect(bad, "one channel alone is not followed", only, "CURRENT", "OK", 1)
    if not only.single_channel:
        bad.append("a step from the only channel is not flagged")
    agreed = update_result(mod, first, [both, both], alone=True)
    expect(bad, "two channels that agree are followed to the end", agreed, "CURRENT", "OK", 4)
    if agreed.single_channel:
        bad.append("single_channel set when every channel agrees")
    behind = update_result(mod, first, [both, {2: both[2]}], alone=True)
    expect(bad, "a channel that is behind stops the chain at the last version both serve", behind, "CURRENT", "OK", 2)
    if not behind.single_channel:
        bad.append("a step the second channel does not yet serve is not flagged")
    expect(
        bad,
        "a lone channel cannot move a root that has expired",
        update_result(mod, first, [both], alone=True, now=R.NOW.replace(year=R.NOW.year + 50)),
        "EXPIRED",
        mod.EXPIRED,
        1,
    )

    # rotation of the root keys: both thresholds must be met
    rotated = document(version=2, root_keys=[KEY2])
    expect(bad, "rotation signed by both", update_result(mod, first, [channel([rotated], [KEY1, KEY2])]), "CURRENT", "OK", 2)
    expect(
        bad,
        "rotation signed by the old key only",
        update_result(mod, first, [channel([rotated], [KEY1])]),
        "REJECTED",
        mod.NEW_THRESHOLD,
        1,
    )
    expect(
        bad,
        "rotation signed by the new key only",
        update_result(mod, first, [channel([rotated], [KEY2])]),
        "REJECTED",
        mod.OLD_THRESHOLD,
        1,
    )
    expect(bad, "rotation signed by a stranger", update_result(mod, first, [channel([rotated], [KEY3])]), "REJECTED", mod.OLD_THRESHOLD, 1)
    expect(bad, "no signatures", update_result(mod, first, [channel([rotated], [])]), "REJECTED", mod.OLD_THRESHOLD, 1)
    after_rotation = [rotated, document(version=3, root_keys=[KEY2])]
    expect(
        bad,
        "the old key cannot sign the step after a rotation",
        update_result(mod, first, [{**channel([rotated], [KEY1, KEY2]), **channel([after_rotation[1]], [KEY1])}]),
        "REJECTED",
        mod.OLD_THRESHOLD,
        2,
    )

    # a signature that does not verify counts for nothing, even under a root key's id
    forged = R.trusted_root.sign_envelope(canonical.dumps(docs[1]), [KEY1])  # type: ignore[arg-type]
    forged["signatures"][0]["sig_b64"] = base64.b64encode(b"\x01" * 64).decode()  # type: ignore[index]
    expect(bad, "a forged signature under a root key id", update_result(mod, first, [{2: forged}]), "REJECTED", mod.OLD_THRESHOLD, 1)
    wrong_payload = R.trusted_root.sign_envelope(canonical.dumps(docs[2]), [KEY1])  # type: ignore[arg-type]
    wrong_payload["payload_b64"] = R.trusted_root.sign_envelope(canonical.dumps(docs[1]), [KEY1])["payload_b64"]  # type: ignore[index]
    expect(bad, "a signature over another document", update_result(mod, first, [{2: wrong_payload}]), "REJECTED", mod.OLD_THRESHOLD, 1)

    # a threshold of two counts each key once
    two_of_two = document(version=1, root_keys=[KEY1, KEY2], threshold=2)
    step = document(version=2, root_keys=[KEY1, KEY2], threshold=2)
    expect(bad, "threshold 2 met by two keys", update_result(mod, two_of_two, [channel([step], [KEY1, KEY2])]), "CURRENT", "OK", 2)
    expect(
        bad,
        "threshold 2 with one key signing twice",
        update_result(mod, two_of_two, [channel([step], [KEY1, KEY1])]),
        "REJECTED",
        mod.OLD_THRESHOLD,
        1,
    )
    expect(bad, "threshold 2 with one key", update_result(mod, two_of_two, [channel([step], [KEY1])]), "REJECTED", mod.OLD_THRESHOLD, 1)
    # signatures over one document add up across channels
    expect(
        bad,
        "signatures from two channels add up",
        update_result(mod, two_of_two, [channel([step], [KEY1]), channel([step], [KEY2])]),
        "CURRENT",
        "OK",
        2,
    )
    envelope_k1 = channel([step], [KEY1])
    envelope_k2_stranger = channel([step], [KEY1, KEY3])
    expect(
        bad,
        "a stranger's signature adds nothing",
        update_result(mod, two_of_two, [envelope_k1, envelope_k2_stranger]),
        "REJECTED",
        mod.OLD_THRESHOLD,
        1,
    )

    # versions go up by exactly one
    wrong_step = copy_with_version(docs[1], 3)
    expect(
        bad,
        "version 3 served as version 2",
        update_result(mod, first, [{2: R.envelope(wrong_step, [KEY1])}]),
        "REJECTED",
        mod.VERSION_STEP,
        1,
    )
    expect(
        bad,
        "version 1 served as version 2",
        update_result(mod, first, [{2: R.envelope(copy_with_version(docs[1], 1), [KEY1])}]),
        "REJECTED",
        mod.VERSION_STEP,
        1,
    )

    # two channels, two documents
    other_two = document(version=2, expires="2027-12-31T00:00:00Z")
    fork = update_result(mod, first, [{2: both[2]}, {2: R.envelope(other_two, [KEY1])}])
    expect(bad, "fork between channels", fork, "FORK", mod.FORK, 1)
    garbage = update_result(mod, first, [{2: both[2]}, {2: {"payload_b64": "!!", "signatures": []}}])
    expect(bad, "a document against garbage", garbage, "FORK", mod.FORK, 1)

    # base64 must be canonical everywhere it is read
    padded = document(version=2)
    for fraction in ("", ".0", ".00", ".000"):  # the same document with a longer expiry text, until its base64 has padding bits
        padded["expires"] = f"2027-10-29T00:00:00{fraction}Z"
        if len(canonical.dumps(padded)) % 3:  # type: ignore[arg-type]
            break
    sig_envelope = R.trusted_root.sign_envelope(canonical.dumps(padded), [KEY1])  # type: ignore[arg-type]
    for label, change in (
        ("a newline in the payload", lambda e: e.__setitem__("payload_b64", e["payload_b64"] + "\n")),
        ("padding bits set in the payload", lambda e: e.__setitem__("payload_b64", noncanonical(e["payload_b64"]))),
        (
            "padding bits set in a signature",
            lambda e: e["signatures"][0].__setitem__("sig_b64", noncanonical(e["signatures"][0]["sig_b64"])),
        ),
        ("an extra member in a signature", lambda e: e["signatures"][0].__setitem__("extra", 1)),
        ("more signatures than allowed", lambda e: e.__setitem__("signatures", e["signatures"] * (mod.MAX_SIGNATURES + 1))),
    ):
        broken_envelope = copy.deepcopy(sig_envelope)
        change(broken_envelope)
        expect(bad, label, update_result(mod, first, [{2: broken_envelope}]), "REJECTED", mod.MALFORMED, 1)

    # malformed pieces never crash
    for label, candidate in (
        ("an envelope that is a list", [1, 2]),
        ("an envelope with no signatures member", {"payload_b64": both[2]["payload_b64"]}),  # type: ignore[index]
        ("an envelope with an extra member", {**both[2], "extra": 1}),  # type: ignore[dict-item]
        ("signatures that are not a list", {"payload_b64": both[2]["payload_b64"], "signatures": "x"}),  # type: ignore[index]
        ("a signature with a short key id", {"payload_b64": both[2]["payload_b64"], "signatures": [{"keyid": "ab", "sig_b64": "AA=="}]}),  # type: ignore[index]
        (
            "a signature of the wrong length",
            {"payload_b64": both[2]["payload_b64"], "signatures": [{"keyid": "0" * 64, "sig_b64": "AA=="}]},
        ),  # type: ignore[index]
        ("a payload that is not base64", {"payload_b64": "###", "signatures": []}),
        ("a payload that is not a document", {"payload_b64": base64.b64encode(b"{}").decode(), "signatures": []}),
    ):
        expect(bad, label, update_result(mod, first, [{2: candidate}]), "REJECTED", mod.MALFORMED, 1)

    # a signed but unacceptable document: rejected by its own parse, however well signed
    for label, change in (
        ("threshold 0", setter(["root_threshold"], 0)),
        ("no threshold", remover(["root_threshold"])),
        ("format version 99", setter(["format_version"], 99)),
        ("an unknown member", setter(["unknown"], 1)),
    ):
        broken = copy.deepcopy(docs[1])
        change(broken)
        envelope = R.trusted_root.sign_envelope(canonical.dumps(broken), [KEY1])  # type: ignore[arg-type]
        expect(bad, f"signed document with {label}", update_result(mod, first, [{2: envelope}]), "REJECTED", mod.MALFORMED, 1)

    # expiry: only the final root counts, against the fixed start time
    stale = [document(version=2, expires="2026-06-01T00:00:00Z")]
    expect(bad, "final root expired", update_result(mod, first, [channel(stale, [KEY1])]), "EXPIRED", mod.EXPIRED, 2)
    fresh_after_stale = [document(version=2, expires="2026-06-01T00:00:00Z"), document(version=3)]
    expect(
        bad,
        "an expired middle root is fine when the last is current",
        update_result(mod, first, [channel(fresh_after_stale, [KEY1])]),
        "CURRENT",
        "OK",
        3,
    )
    expired_initial = document(version=1, expires="2026-06-01T00:00:00Z")
    expect(bad, "the shipped root has expired", update_result(mod, expired_initial, [{}]), "EXPIRED", mod.EXPIRED, 1)
    on_the_second = document(version=1, expires="2026-10-01T12:00:00Z")
    expect(bad, "expiry at the start time counts as expired", update_result(mod, on_the_second, [{}]), "EXPIRED", mod.EXPIRED, 1)
    expect(
        bad,
        "the start time decides",
        update_result(mod, first, [{}], now=datetime.fromisoformat("2028-01-01T00:00:00+00:00")),
        "EXPIRED",
        mod.EXPIRED,
        1,
    )
    try:
        update_result(mod, first, [{}], now=datetime(2026, 10, 1, 12, 0, 0))
        bad.append("a start time without a zone was accepted")
    except ValueError:
        pass

    # bounded chains and rollback
    expect(bad, "chain longer than the bound", update_result(mod, first, [both], max_chain=2), "REJECTED", mod.CHAIN_BOUND, 3)
    expect(bad, "chain exactly at the bound", update_result(mod, first, [channel(docs[:3], [KEY1])], max_chain=2), "CURRENT", "OK", 3)
    expect(bad, "a shipped root below the floor", update_result(mod, first, [{}], floor_version=2), "REJECTED", mod.ROLLBACK, 1)
    expect(bad, "a floor reached through the chain", update_result(mod, first, [both], floor_version=2), "CURRENT", "OK", 4)
    expect(bad, "a floor above the final version", update_result(mod, first, [{2: both[2]}], floor_version=3), "REJECTED", mod.ROLLBACK, 2)
    return bad


def noncanonical(text: str) -> str:
    """The same bytes spelled with different unused bits in the last base64 digit."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    position = len(text.rstrip("=")) - 1
    original = base64.b64decode(text)
    for char in alphabet:
        candidate = text[:position] + char + text[position + 1 :]
        if candidate != text and base64.b64decode(candidate) == original:
            return candidate
    raise AssertionError("no non-canonical spelling exists for this text")


def copy_with_version(doc: dict[str, object], version: int) -> dict[str, object]:
    clone = copy.deepcopy(doc)
    clone["version"] = version
    return clone


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "parses_valid_documents": parses_valid_documents,
    "rejects_documents": rejects_documents,
    "update_chain": update_chain,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
