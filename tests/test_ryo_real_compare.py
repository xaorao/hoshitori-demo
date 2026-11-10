"""The one real answer of ``compare_tokens`` (a private live recording, skipped where it is not on the machine): the field table written from the
catalogue's schema fits it without drift, a real null stays unknown, and the synthetic answer of the tests has the same shape as the real one."""

from __future__ import annotations

from collections import Counter
from typing import Any

from hoshitori import ryo_evidence, ryo_fields
from hoshitori.capture import RawCapture
from hoshitori.ryo_envelope import read_rest_success
from tests import ryo_data as D
from tests import ryo_support
from tests.ryo_evidence_checks import RECEIVED, SENT

ASKED = {"symbols": "SOL, AVAX", "intent": "swing"}


def real() -> dict[str, Any]:
    return ryo_support.live_fixture("call_compare_tokens_ok")


def shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return "number" if type(value).__name__ in ("int", "float", "Num") and not isinstance(value, bool) else type(value).__name__


def test_the_recording_is_a_live_answer_that_says_so() -> None:
    rec = real()
    assert (rec["request"]["body"], rec["response"]["status"]) == (ASKED, 200)
    envelope = read_rest_success(rec["raw"], "compare_tokens")
    assert (envelope.status, envelope.data_mode) == ("ok", "live")
    assert envelope.availability == {
        "token_market_data": "available",
        "comparison_factors": "available",
        "token_profiles": "partial",
        "conclusion": "available",
    }
    assert [t["symbol"] for t in envelope.data["tokens"]] == ["SOL", "AVAX"]


def test_the_field_table_fits_the_real_answer_with_nothing_missing_and_nothing_unexpected() -> None:
    envelope = read_rest_success(real()["raw"], "compare_tokens")
    walked = ryo_fields.walk("compare_tokens", envelope.data)
    assert list(walked.missing) == []
    assert Counter(f.klass.name for f in walked.fields) == {"OBSERVATION": 26, "INTERPRETATION": 17, "META": 9}
    interpretation = sorted(f.path for f in walked.fields if f.klass.name == "INTERPRETATION" and f.path.startswith("conclusion"))
    assert interpretation == [f"conclusion.{k}" for k in ("confidence", "headline", "method", "pick", "rationale", "runner_up_case")]


def test_the_real_answer_becomes_an_observation_and_an_interpretation_and_a_real_null_stays_unknown() -> None:
    rec = real()
    capture = RawCapture(
        "cap-" + "1" * 32,
        "ryo",
        {"operation": "compare_tokens", "arguments": ASKED},
        200,
        {"content-type": "application/json"},
        rec["raw"],
        SENT,
        RECEIVED,
        trace_id="0123456789abcdef0123456789abcdef",
    )
    made = ryo_evidence.items_from(capture, "compare_tokens", ASKED, "ryo-evidence-1")
    assert (len(made.observation.fields), len(made.observation.unknown_fields)) == (22, 4)
    assert made.interpretation is not None and (len(made.interpretation.fields), len(made.interpretation.unknown_fields)) == (17, 0)
    unknown = dict(made.observation.unknown_fields)
    assert unknown["tokens[1].metrics.atr_14_pct"].value == "NULL"  # AVAX's ATR was null in the real answer: unknown, never zero
    assert "tokens[1].metrics.atr_14_pct" not in dict(made.observation.fields)
    assert not any("conclusion" in p or "winner" in p for p, _ in made.observation.fields)  # RYO's pick is interpretation only


def test_the_synthetic_answer_of_the_tests_has_the_real_shape() -> None:
    real_data = read_rest_success(real()["raw"], "compare_tokens")
    # the shape of both, as the project's own reader sees it; the one difference is the opaque profile, empty in the synthetic answer
    synthetic = shape(D.compare_tokens_data())
    actual = shape(dict(real_data.data))

    def differences(a: Any, b: Any, path: str = "") -> list[str]:
        if isinstance(a, dict) and isinstance(b, dict):
            out = [f"only in one: {path}{k}" for k in sorted(set(a) ^ set(b))]
            for k in sorted(set(a) & set(b)):
                out += differences(a[k], b[k], f"{path}{k}.")
            return out
        if isinstance(a, list) and isinstance(b, list):
            return differences(a[0], b[0], f"{path}[].") if a and b else []
        return [] if a == b else [f"{path}: {a} against {b}"]

    found = differences(synthetic, actual)
    assert [d for d in found if "token_profile" not in d] == []
