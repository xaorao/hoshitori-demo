"""MCP and REST front ends against a real server on 127.0.0.1: the checks pass on the real module and every small change of its code is
noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import server
from tests.mutation_sweep import survivors
from tests.server_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(server) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "decode_header_value|True<->False|True|0": (
        "validate=False: the payload already matched _BASE64 (alphabet characters, correct padding), so validate=True has nothing to refuse"
    ),
    "decode_header_value|str+~|'utf-8'|0": "Python's codec lookup normalises the name 'utf-8~' to 'utf-8' (codecs.lookup('utf-8~').name == 'utf-8')",
    "make_http_handler.Handler._gate|return None|return False|0": "every caller tests `not self._gate()`, and None is as false as False",
    "make_http_handler.Handler._read_json|Gt->GtE|remaining > 0|0": (
        "at remaining == 0 the extra pass reads 0 bytes, gets b'' and breaks; nothing else changes"
    ),
    "make_http_handler.Handler._read_json|int+1|1|0": "length = -2: like -1 it takes the `length < 0` branch, and nothing else reads it",
    "|statement dropped|Runner = Callable[[object], dict[str, Any]]|0": (
        "a type alias used only in annotations, which `from __future__ import annotations` never evaluates"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.server", "tests.server_checks")
    assert total > 500, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
