"""MCP and REST front ends: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from mutation_sweep import survivors
from server_checks import failures

from indicator_condition_check import server


def test_real_module_passes_every_check() -> None:
    assert failures(server) == []


_ALIAS = "a type alias that only the type checker reads (the module has `from __future__ import annotations`, so annotations stay text)"
_OUTSIDE = "inside `if not 0 <= length <= MAX_BODY` the length is below zero or above MAX_BODY, never equal to it, so > and >= agree"
_DRAIN = (
    "every read of the drain is a blocking read of a buffered socket file, which returns short only at the end of the stream, and asks "
    "for no more than what remains"
)

# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_valid_modern_claim|return None|return False|0": (
        "its one caller, _answer_one, tests `not _valid_modern_claim(params)`, and None and False are both false"
    ),
    "decode_header_value|str+~|'utf-8'|0": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "make_http_handler.Handler._gate|return None|return False|0": (
        "every caller tests `if not self._gate(): return`, and None and False are both false"
    ),
    "make_http_handler.Handler._read_body|Gt->GtE|length > MAX_BODY|0": _OUTSIDE,
    "make_http_handler.Handler._read_body|Gt->GtE|length > MAX_BODY|1": _OUTSIDE,
    "make_http_handler.Handler._read_body|Gt->GtE|remaining > 0|0": (
        "with nothing left the loop runs once more and reads min(65536, 0) bytes: read(0) returns b'' at once and the loop breaks, so the "
        "same bytes are read and the same answer is sent"
    ),
    "make_http_handler.Handler._read_body|int+1|1|0": "a length that is not a number becomes -2 instead of -1: both are below zero, the same 400",
    "make_http_handler.Handler._read_body|int+1|65536|0": (
        f"the size of each read of the drain, 65537 bytes instead of 65536: {_DRAIN}, so the same bytes are drained and the answer waits for "
        "the same last byte"
    ),
    "make_http_handler.Handler._read_body|min->max|min(65536, remaining)|0": (
        "the drain starts with at least 65537 bytes to read (length > MAX_BODY), so max reads all of them in one read instead of reads of "
        f"64 KiB; {_DRAIN} (after a short read the next returns b'' and the loop breaks): the same bytes, the same answer"
    ),
    "make_http_handler.Handler._read_body|statement dropped|self.close_connection = True|0": (
        "the Handler keeps BaseHTTPRequestHandler's protocol_version, HTTP/1.0, for which parse_request has already set close_connection "
        "(it is cleared only for HTTP/1.1), so every connection carries one request either way; part http checks that a keep-alive request "
        "still gets one answer and a closed connection"
    ),
    "|statement dropped|Answer = tuple[int, Any]|0": _ALIAS,
    "|statement dropped|Headers = Mapping[str, str]|0": _ALIAS,
    "|statement dropped|Runner = Callable[[dict[str, Any]], dict[str, Any]]|0": _ALIAS,
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("indicator_condition_check.server", "server_checks")
    assert total > 750, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
