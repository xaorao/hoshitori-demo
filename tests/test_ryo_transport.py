"""The RYO transport and the recorded mode against a fake server on loopback: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import ryo_transport
from tests.mutation import mutant
from tests.ryo_transport_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_transport) == []


# Redundant guards without mutants, each because another rule already decides the same outcome:
# * a comment line in a key file (its name part, "# RYO_MCP_KEY", can never equal the key's name);
# * the operation and arguments type test of the recordings (only needed for typing, `read_answer` refuses anything else).

MUTATIONS: dict[str, tuple[str, str]] = {
    "a missing key file raises": ("    if not path.is_file():\n        return None\n    for line", "    for line"),
    "lines without an equals sign are read": (
        '        if not text or text.startswith("#") or "=" not in text:',
        '        if not text or text.startswith("#"):',
    ),
    "recordings are read without checking the answer": (
        "            try:\n                read_answer(operation, capture.body)\n            except ConnectorError:\n                continue\n",
        "",
    ),
    "recordings are only checked for tools": (
        "                read_answer(operation, capture.body)",
        "                read_answer(operation, capture.body) if operation in ('analyze_token', 'scan_market', 'deep_analysis', 'compare_tokens', 'market_overview', 'monitor_market_sentiment_shift') else None",
    ),
    "the first match does not win": (
        "            return value or None\n    return None",
        "            result = value or None\n    return None",
    ),
    "the name is matched in part": ("        if key.strip() == name:", "        if name in key:"),
    "the key is not trimmed": (
        "        if key.strip() == name:\n            value = value.strip()",
        "        if key.strip() == name:\n            value = value",
    ),
    "the name is not trimmed": ("        if key.strip() == name:", "        if key == name:"),
    "quotes are kept": ("                value = value[1:-1]\n", "                pass\n"),
    "mismatched quotes are stripped": (
        '            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\"\'":',
        '            if len(value) >= 2 and value[0] in "\\"\'":',
    ),
    "a single quote is stripped": (
        '            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\"\'":',
        '            if len(value) >= 1 and value[0] == value[-1] and value[0] in "\\"\'":',
    ),
    "an empty value is a key": ("            return value or None\n", "            return value\n"),
    "the name parameter is ignored": ("        if key.strip() == name:", '        if key.strip() == "RYO_MCP_KEY":'),
    "a free read takes arguments": (
        '            if args:\n                raise TransportFailure(ErrorClass.INVALID_ARGS, f"{operation} takes no arguments")\n',
        "",
    ),
    "a free read is a POST": (
        '            return "GET", self._prefix + META_OPERATIONS[operation], None',
        '            return "POST", self._prefix + META_OPERATIONS[operation], b"{}"',
    ),
    "a free read goes to the wrong path": (
        '            return "GET", self._prefix + META_OPERATIONS[operation], None',
        '            return "GET", META_OPERATIONS[operation], None',
    ),
    "the operation name is not checked": (
        '        if TOOL_NAME.fullmatch(operation) is None:\n            raise TransportFailure(ErrorClass.INVALID_ARGS, "the operation is not a tool name")\n',
        "",
    ),
    "the operation name is matched in part": (
        "        if TOOL_NAME.fullmatch(operation) is None:",
        "        if TOOL_NAME.match(operation) is None:",
    ),
    "a tool call is a GET": (
        '        return "POST", f"{self._prefix}/tools/{operation}/call", canonical.dumps(dict(args))',
        '        return "GET", f"{self._prefix}/tools/{operation}/call", None',
    ),
    "arguments are sent in the order given": (
        "canonical.dumps(dict(args))\n\n    def _auth_header",
        "json.dumps(dict(args)).encode()\n\n    def _auth_header",
    ),
    "the health read sends the key": ('NO_AUTH: Final = frozenset({"_health"})', "NO_AUTH: Final = frozenset()"),
    "everything is sent without the key": (
        "        if operation in NO_AUTH:\n            return {}",
        "        if True:\n            return {}",
    ),
    "a missing key is sent anyway": (
        '        if not key:\n            raise TransportFailure(ErrorClass.AUTH, "no builder key is configured")\n',
        "",
    ),
    "a bad key is sent anyway": (
        '        if KEY_SHAPE.fullmatch(key) is None:\n            raise TransportFailure(ErrorClass.AUTH, "the builder key is not a usable header value")\n',
        "",
    ),
    "a key is matched in part": ("        if KEY_SHAPE.fullmatch(key) is None:", "        if KEY_SHAPE.match(key) is None:"),
    "a key may contain whitespace": (
        'KEY_SHAPE: Final = re.compile(r"[\\x21-\\x7e]{8,512}")',
        'KEY_SHAPE: Final = re.compile(r"[\\x20-\\x7e]{8,512}")',
    ),
    "a key may be shorter": ('r"[\\x21-\\x7e]{8,512}"', 'r"[\\x21-\\x7e]{3,512}"'),
    "a key may be longer": ('r"[\\x21-\\x7e]{8,512}"', 'r"[\\x21-\\x7e]{8,2000}"'),
    "a key may be a little too long": ('r"[\\x21-\\x7e]{8,512}"', 'r"[\\x21-\\x7e]{8,511}"'),
    "a key must be a little longer": ('r"[\\x21-\\x7e]{8,512}"', 'r"[\\x21-\\x7e]{9,512}"'),
    "the bearer word is missing": ('{"Authorization": f"Bearer {key}"}', '{"Authorization": key}'),
    "recordings are not keyed by operation": (
        "    return operation, canonical.dumps(dict(args)).decode()",
        '    return "", canonical.dumps(dict(args)).decode()',
    ),
    "recordings are keyed by the order of arguments": (
        "    return operation, canonical.dumps(dict(args)).decode()",
        "    return operation, repr(sorted(args.items(), reverse=True)) if False else operation + repr(list(args.items()))",
    ),
    "recordings are not counted": ("        return len(self._by_key)", "        return 0"),
    "the first recording wins": (
        "        self._by_key[recording_key(operation, args)] = recorded",
        "        self._by_key.setdefault(recording_key(operation, args), recorded)",
    ),
    "other providers' captures are used": (
        "            if capture.source != provider or capture.status != 200 or not isinstance(operation, str) or not isinstance(arguments, dict):",
        "            if capture.status != 200 or not isinstance(operation, str) or not isinstance(arguments, dict):",
    ),
    "failed captures are used": (
        "            if capture.source != provider or capture.status != 200 or not isinstance(operation, str) or not isinstance(arguments, dict):",
        "            if capture.source != provider or not isinstance(operation, str) or not isinstance(arguments, dict):",
    ),
    "a capture with no arguments is used": (
        "or capture.status != 200 or not isinstance(operation, str) or not isinstance(arguments, dict):",
        "or capture.status != 200 or not isinstance(operation, str):",
    ),
    "a recording is timed when first recorded": (
        "        received = max(self._clock(), sent_at)\n        headers = dict(recorded.headers)",
        "        received = sent_at\n        headers = dict(recorded.headers)",
    ),
    "the recorded answer is edited": (
        "            recorded.body,\n            sent_at,",
        "            recorded.body + b' ',\n            sent_at,",
    ),
    "a replay is not labelled": ('            "mode": "recorded",\n', ""),
    "a replay does not say what it replays": ('            "recorded_from": recorded.capture_id,\n', ""),
    "a replay is said to be live": ("provider: str = RECORDED_PROVIDER) -> None:", "provider: str = PROVIDER) -> None:"),
    "a missing recording is invented": (
        '            raise TransportFailure(ErrorClass.NOT_FOUND, "there is no recording of this request")',
        '            return RawCapture(capture_id, self._provider, {"operation": operation}, 200, {}, b"{}", sent_at, sent_at, None)',
    ),
    "a missing recording is retried": (
        'raise TransportFailure(ErrorClass.NOT_FOUND, "there is no recording of this request")',
        'raise TransportFailure(ErrorClass.UPSTREAM, "there is no recording of this request")',
    ),
}

# Which parts of the scenarios can notice each mutant. A mutant is run only against these (a part that starts a server costs about a second);
# the full set runs against the real module.
PARTS: dict[str, tuple[str, ...]] = {}


def _assign(parts: tuple[str, ...], *names: str) -> None:
    for name in names:
        assert name in MUTATIONS, name
        assert name not in PARTS, name
        PARTS[name] = parts


_assign(
    ("keyfile",),
    "a missing key file raises",
    "lines without an equals sign are read",
    "the first match does not win",
    "the name is matched in part",
    "the key is not trimmed",
    "the name is not trimmed",
    "quotes are kept",
    "mismatched quotes are stripped",
    "a single quote is stripped",
    "an empty value is a key",
    "the name parameter is ignored",
)
_assign(
    ("recorded",),
    "recordings are read without checking the answer",
    "recordings are only checked for tools",
    "recordings are not keyed by operation",
    "recordings are keyed by the order of arguments",
    "recordings are not counted",
    "the first recording wins",
    "other providers' captures are used",
    "failed captures are used",
    "a capture with no arguments is used",
    "a recording is timed when first recorded",
    "the recorded answer is edited",
    "a replay is not labelled",
    "a replay does not say what it replays",
    "a replay is said to be live",
    "a missing recording is invented",
    "a missing recording is retried",
)
_assign(
    ("request",),
    "a free read takes arguments",
    "a free read is a POST",
    "a free read goes to the wrong path",
    "the operation name is not checked",
    "the operation name is matched in part",
    "a tool call is a GET",
    "arguments are sent in the order given",
    "the health read sends the key",
    "everything is sent without the key",
    "a missing key is sent anyway",
    "a bad key is sent anyway",
    "a key is matched in part",
    "a key may contain whitespace",
    "a key may be shorter",
    "a key may be longer",
    "a key may be a little too long",
    "a key must be a little longer",
    "the bearer word is missing",
)


def test_every_mutant_has_the_parts_that_can_notice_it() -> None:
    assert set(PARTS) == set(MUTATIONS)


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.ryo_transport", old, new)
    try:
        found = failures(module, PARTS[name])
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
