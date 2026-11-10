"""The safe fetch client's rules: every check passes on the real module, and every small change of its code is noticed (or is harmless, and says why)."""

from __future__ import annotations

from hoshitori import fetch_types, safe_fetch
from hoshitori.connector import ErrorClass
from tests.mutation_sweep import survivors
from tests.safe_fetch_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(safe_fetch) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "is_public_address|statement dropped|ip = ip.ipv4_mapped|0": "Python 3.12's is_global already looks inside an IPv4-mapped address; the unwrapping is kept so that older versions judge it the same way",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.safe_fetch", "tests.safe_fetch_checks")
    assert total > 250, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_every_reason_has_a_class_and_the_classes_are_the_documented_ones() -> None:
    codes = set(fetch_types.FetchCode)
    assert set(fetch_types.CLASS_OF) == codes
    refusals = {
        "INVALID_URL",
        "SCHEME",
        "HTTP_NOT_ALLOWED",
        "CREDENTIALS",
        "PORT",
        "HOST_INVALID",
        "HOST_NOT_ALLOWED",
        "ADDRESS_NOT_PUBLIC",
    }
    assert {
        c.value for c in codes if fetch_types.CLASS_OF[c] is ErrorClass.INVALID_ARGS
    } == refusals  # a refusal is the caller's input, never retried
    assert fetch_types.CLASS_OF[fetch_types.FetchCode.TIMEOUT] is ErrorClass.TIMEOUT
    assert fetch_types.CLASS_OF[fetch_types.FetchCode.DNS] is ErrorClass.NOT_FOUND
    for code in (
        fetch_types.FetchCode.TOO_LARGE,
        fetch_types.FetchCode.CONTENT_TYPE,
        fetch_types.FetchCode.CONTENT_ENCODING,
        fetch_types.FetchCode.PROTOCOL,
    ):
        assert fetch_types.CLASS_OF[code] is ErrorClass.MALFORMED, code
    assert {c.value: c.value for c in codes} == {c.name: c.value for c in codes}, "every code's value is its name"
    error = fetch_types.FetchError(fetch_types.FetchCode.HTTP_STATUS, "x", status=None)
    assert error.error_class is ErrorClass.UPSTREAM and error.events == ()
