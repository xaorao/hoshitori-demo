"""The Hypothesis Laboratory core (product description 4.20): a typed mandate in the one condition grammar, every missing choice listed and never
completed silently, a frozen version, a preview that says what it is, and a monitor of the invalidation condition that never turns unknown into false."""

from __future__ import annotations

import ast
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hoshitori import lab

DROP = object()  # a field left out of a proposal
CONDITION = {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 30}}
INVALIDATION = {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}}
ASSUMPTIONS = [
    {"id": "a1", "kind": "OBSERVATION", "text": "SOL is oversold on the hourly RSI"},
    {"id": "a2", "kind": "CAUSAL", "text": "oversold readings are followed by a bounce"},
]


def raw(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statement": "SOL bounces after an oversold hourly RSI",
        "symbol": "SOL",
        "direction": "UP",
        "horizon_h": 4,
        "condition": CONDITION,
        "invalidation": INVALIDATION,
        "max_weight": "0.02",
        "assumptions": ASSUMPTIONS,
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not DROP}


def bars(closes: list[float]) -> Any:
    from indicator_condition_check.condition import Bars

    n = len(closes)
    return Bars([1_000_000 + 3_600_000 * i for i in range(n)], closes, closes, closes, closes, [1.0] * n)


def refuses(call: object, says: str) -> None:
    with pytest.raises(lab.LabError) as raised:
        call()  # type: ignore[operator]
    assert str(raised.value) == says


def test_a_complete_proposal_becomes_a_mandate_with_nothing_filled_in() -> None:
    draft = lab.draft_from(raw())
    assert draft.choices == () and draft.mandate is not None
    m = draft.mandate
    assert (m.symbol, m.direction, m.horizon_h, m.max_weight) == ("SOL", "UP", 4, Decimal("0.02"))
    assert m.condition["op"] == "<" and m.invalidation["right"] == {"value": 90}
    assert [(a.id, a.kind) for a in m.assumptions] == [("a1", "OBSERVATION"), ("a2", "CAUSAL")]


def test_every_missing_or_ambiguous_part_is_a_choice_for_the_person_and_no_mandate_is_made() -> None:
    for field, choice in (
        ("statement", "the statement of the hypothesis"),
        ("symbol", "the token the hypothesis is about"),
        ("direction", "the direction (UP or DOWN)"),
        ("horizon_h", "the horizon in hours (1, 4 or 24)"),
        ("condition", "the condition that opens a forecast"),
        ("invalidation", "the condition that invalidates it"),
        ("max_weight", "the largest weight the strategy may hold"),
        ("assumptions", "the assumptions the hypothesis rests on (at least one)"),
    ):
        draft = lab.draft_from(raw(**{field: DROP}))
        assert draft.mandate is None and draft.choices == (f"choose {choice}",), field


def test_values_outside_their_lists_and_limits_are_choices_not_corrected() -> None:
    assert lab.draft_from(raw(direction="SIDEWAYS")).choices == ("the direction is UP or DOWN, not SIDEWAYS",)
    assert lab.draft_from(raw(horizon_h=2)).choices == ("the horizon is 1, 4 or 24 hours, not 2",)
    assert lab.draft_from(raw(symbol="sol")).choices == ("the token is 1 to 20 capital letters or digits, not 'sol'",)
    assert lab.draft_from(raw(max_weight="0.06")).choices == (
        "the weight 0.06 is above the limit of 0.05; the limit is never applied silently",
    )
    assert lab.draft_from(raw(max_weight="0")).choices == ("the weight is above zero, not 0",)
    assert lab.draft_from(raw(max_weight="a lot")).choices == ("the weight is a decimal number, not 'a lot'",)
    assert lab.draft_from(raw(statement="x" * 401)).choices == ("the statement is 1 to 400 characters",)
    assert lab.draft_from(raw(statement="")).choices == ("the statement is 1 to 400 characters",)
    assert lab.draft_from(raw(max_weight="0.05")).choices == () and lab.draft_from(raw(statement="x" * 400)).choices == ()
    assert lab.draft_from(raw(extra=1)).choices == ("unknown member extra",)


def test_a_condition_outside_the_grammar_is_rejected_with_the_grammar_s_words_and_free_text_is_never_run() -> None:
    bad = lab.draft_from(raw(condition={"left": {"indicator": "vibes"}, "op": "<", "right": {"value": 1}}))
    assert bad.mandate is None and len(bad.choices) == 1 and bad.choices[0].startswith("the condition is not in the grammar: ")
    free = lab.draft_from(raw(condition="__import__('os').system('echo hi')"))
    assert free.mandate is None and free.choices[0].startswith("the condition is not in the grammar: ")
    other = lab.draft_from(raw(invalidation={"all": []}))
    assert other.choices[0].startswith("the invalidation is not in the grammar: ")
    both = lab.draft_from(raw(condition="x", invalidation="y"))
    assert len(both.choices) == 2


def test_the_assumptions_are_typed_with_unique_ids_and_stated_in_words() -> None:
    assert lab.draft_from(raw(assumptions=[])).choices == ("choose the assumptions the hypothesis rests on (at least one)",)
    assert lab.draft_from(raw(assumptions=[{"id": "a1", "kind": "MOOD", "text": "x"}])).choices == (
        f"an assumption's kind is one of {list(lab.ASSUMPTION_KINDS)}, not MOOD",
    )
    dup = [{"id": "a1", "kind": "OBSERVATION", "text": "x"}, {"id": "a1", "kind": "CAUSAL", "text": "y"}]
    assert lab.draft_from(raw(assumptions=dup)).choices == ("an assumption id is used once: a1",)
    assert lab.draft_from(raw(assumptions=[{"id": "a1", "kind": "OBSERVATION", "text": ""}])).choices == (
        "an assumption has 1 to 200 characters of text",
    )
    assert lab.draft_from(raw(assumptions=[{"id": "a1", "kind": "OBSERVATION"}])).choices == ("an assumption has an id, a kind and a text",)
    assert lab.draft_from(raw(assumptions="all of them")).choices == ("the assumptions are a list",)
    too_many = [{"id": f"a{k}", "kind": "OBSERVATION", "text": "x"} for k in range(9)]
    assert lab.draft_from(raw(assumptions=too_many)).choices == ("a mandate has at most 8 assumptions",)
    assert lab.draft_from(raw(assumptions=too_many[:8])).choices == ()


def make(tmp_path: Path) -> lab.Lab:
    return lab.Lab(tmp_path / "lab.jsonl")


def test_a_confirmed_mandate_is_frozen_as_a_version_and_an_edit_is_a_new_version_with_a_parent(tmp_path: Path) -> None:
    book = make(tmp_path)
    draft = lab.draft_from(raw())
    v1 = book.freeze(draft, "alice")
    text = json.dumps({"mandate": draft.mandate.to_record(), "parent": None}, sort_keys=True, separators=(",", ":"))  # type: ignore[union-attr]
    assert v1 == hashlib.sha256(text.encode()).hexdigest()
    edited = lab.draft_from(raw(horizon_h=24))
    v2 = book.freeze(edited, "alice", parent=v1)
    versions = book.versions()
    assert versions[v1].parent is None and versions[v2].parent == v1 and versions[v2].by == "alice" and versions[v1].mandate.horizon_h == 4
    assert make(tmp_path).versions().keys() == {v1, v2}  # the book is rebuilt from its log
    refuses(lambda: book.freeze(draft, "alice"), "this version is already frozen")
    refuses(lambda: book.freeze(lab.draft_from(raw(symbol="ETH")), "alice", parent="f" * 64), "the parent version does not exist")
    refuses(lambda: book.freeze(lab.draft_from(raw(symbol="ETH")), ""), "a version is frozen by someone")
    refuses(
        lambda: book.freeze(lab.draft_from(raw(direction=DROP)), "alice"),
        "the draft still has choices to confirm: choose the direction (UP or DOWN)",
    )


def test_a_frozen_version_cannot_be_changed_after_the_fact(tmp_path: Path) -> None:
    book = make(tmp_path)
    v1 = book.freeze(lab.draft_from(raw()), "alice")
    with pytest.raises(AttributeError):
        book.versions()[v1].mandate.symbol = "ETH"  # type: ignore[misc]
    path = tmp_path / "lab.jsonl"
    path.write_text(path.read_text().replace('"SOL"', '"ETH"'))
    from hoshitori.command_log import LogError

    with pytest.raises(LogError):
        make(tmp_path)  # an edited log is refused, so a version's content cannot be rewritten


def test_the_preview_is_labelled_and_reports_three_valued_results_and_its_data_limits(tmp_path: Path) -> None:
    book = make(tmp_path)
    v = book.freeze(lab.draft_from(raw(condition={"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}})), "alice")
    closes = [105.0, 104.0, 99.0, 98.0, 101.0, 102.0]
    out = book.preview(v, bars(closes))
    assert (
        out.label
        == lab.PREVIEW_LABEL
        == "demonstration, not evidence: made on past data, which a model may have seen; no forecast was committed"
    )
    assert (out.bars, out.true_bars, out.false_bars, out.null_bars) == (6, 2, 4, 0)
    assert out.last_true_index == 3 and out.first_ts == 1_000_000 and out.last_ts == 1_000_000 + 5 * 3_600_000
    short = book.preview(book.freeze(lab.draft_from(raw()), "alice"), bars([100.0] * 5))  # the RSI needs more bars than this
    assert (short.true_bars, short.false_bars, short.null_bars) == (0, 0, 5) and short.last_true_index is None


def test_the_monitor_says_unknown_when_it_cannot_tell_and_never_calls_that_holds(tmp_path: Path) -> None:
    book = make(tmp_path)
    v = book.freeze(lab.draft_from(raw()), "alice")  # invalidated when the close is below 90
    assert book.monitor(v, bars([100.0, 95.0, 85.0])).state == "INVALIDATED"
    assert book.monitor(v, bars([100.0, 95.0, 92.0])).state == "HOLDS"
    unknown = book.monitor(
        book.freeze(
            lab.draft_from(raw(invalidation={"left": {"indicator": "sma", "period": 50}, "op": "<", "right": {"value": 90}})), "alice"
        ),
        bars([100.0, 95.0, 92.0]),
    )
    assert unknown.state == "UNKNOWN" and unknown.reason is not None and "50" in unknown.reason
    refuses(lambda: book.monitor("0" * 64, bars([1.0])), "no such version")


def test_the_forward_spec_is_data_only_and_names_the_version(tmp_path: Path) -> None:
    book = make(tmp_path)
    v = book.freeze(lab.draft_from(raw()), "alice")
    spec = book.forward_spec(v)
    assert spec == {
        "kind": "RULE_ARM",
        "version": v,
        "symbol": "SOL",
        "direction": "UP",
        "horizon_h": 4,
        "entry": lab.normalize_condition(CONDITION),
        "invalidation": lab.normalize_condition(INVALIDATION),
        "max_weight": "0.02",
        "frozen_at_ms": None,
    }
    assert spec["entry"]["left"] == {"indicator": "rsi", "period": 14, "source": "close"}  # every default is made explicit
    assert set(spec) == {"kind", "version", "symbol", "direction", "horizon_h", "entry", "invalidation", "max_weight", "frozen_at_ms"}
    assert json.loads(json.dumps(spec)) == spec


def test_a_version_records_the_moment_it_was_frozen_and_the_spec_carries_it(tmp_path: Path) -> None:
    book = make(tmp_path)
    v = book.freeze(lab.draft_from(raw()), "alice", at_ms=1_700_000_000_000)
    assert book.versions()[v].frozen_at_ms == 1_700_000_000_000
    assert book.forward_spec(v)["frozen_at_ms"] == 1_700_000_000_000
    assert make(tmp_path).versions()[v].frozen_at_ms == 1_700_000_000_000  # rebuilt from the log
    assert v == lab._version_hash(lab.draft_from(raw()).mandate.to_record(), None)  # type: ignore[union-attr]  # the time is not part of the hash
    for bad in (-1, True, 1.5, "1"):
        refuses(
            lambda b=bad: make(tmp_path).freeze(lab.draft_from(raw(symbol="ETH")), "alice", at_ms=b),
            "the moment of freezing is whole milliseconds, zero or more",
        )  # type: ignore[arg-type]
    epoch = make(tmp_path)
    z = epoch.freeze(lab.draft_from(raw(symbol="XRP")), "alice", at_ms=0)
    assert epoch.versions()[z].frozen_at_ms == 0 and epoch.forward_spec(z)["frozen_at_ms"] == 0  # the epoch is a moment
    grammar = lab.grammar()
    assert callable(grammar.evaluate) and grammar.Bars is not None and grammar.OPS[0] == "<"  # the grammar's own module
    other = make(tmp_path)
    w = other.freeze(lab.draft_from(raw(symbol="BTC")), "alice")
    assert other.versions()[w].frozen_at_ms is None and other.forward_spec(w)["frozen_at_ms"] is None  # an older version has no time


def test_nothing_in_the_laboratory_can_run_generated_code() -> None:
    tree = ast.parse(Path(lab.__file__).read_text())
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not called & {"eval", "exec", "compile", "__import__", "open"}
    assert not attributes & {"system", "popen", "Popen", "run", "check_output"}
    assert not imported & {"subprocess", "os", "importlib", "ctypes", "pickle"}


def test_the_numbers_of_the_laboratory_are_pinned() -> None:
    assert lab.HORIZONS == (1, 4, 24) and lab.DIRECTIONS == ("UP", "DOWN") and Decimal("0.05") == lab.MAX_WEIGHT
    assert lab.ASSUMPTION_KINDS == ("OBSERVATION", "INTERPRETATION", "CAUSAL", "FORECAST")
    assert (lab.MAX_STATEMENT, lab.MAX_ASSUMPTIONS, lab.MAX_ASSUMPTION_TEXT) == (400, 8, 200)


def test_a_draft_with_several_choices_lists_them_all_and_a_log_line_names_its_operation(tmp_path: Path) -> None:
    from hoshitori.command_log import CommandLog

    book = make(tmp_path)
    refuses(
        lambda: book.freeze(lab.draft_from(raw(symbol=DROP, direction=DROP)), "alice"),
        "the draft still has choices to confirm: choose the token the hypothesis is about; choose the direction (UP or DOWN)",
    )
    book.freeze(lab.draft_from(raw()), "alice")
    commands = CommandLog(tmp_path / "lab.jsonl").read()
    assert [c["op"] for c in commands] == ["freeze"] and set(commands[0]) == {"op", "mandate", "parent", "by", "at_ms"}


def test_the_preview_refuses_no_bars_and_the_monitor_says_why_it_cannot_tell(tmp_path: Path) -> None:
    book = make(tmp_path)
    v = book.freeze(lab.draft_from(raw()), "alice")
    refuses(lambda: book.preview(v, bars([])), "no bars to preview")
    empty = book.monitor(v, bars([]))
    assert (empty.state, empty.reason) == ("UNKNOWN", "the condition cannot be decided on the last bar")
    one = book.preview(v, bars([100.0]))
    assert (one.bars, one.first_ts, one.last_ts) == (1, 1_000_000, 1_000_000)


def test_a_missing_grammar_is_an_error_of_the_laboratory_and_never_a_choice_for_the_person() -> None:
    import sys

    saved = sys.modules.get("indicator_condition_check")
    sys.modules["indicator_condition_check"] = None  # type: ignore[assignment]
    try:
        refuses(
            lambda: lab.normalize_condition(CONDITION),
            "the indicator condition grammar is not installed, so a condition cannot be validated; install the skill beside this package: pip install ./skills/indicator-condition-check",
        )
        refuses(lambda: lab.draft_from(raw()), lab.GRAMMAR_MISSING)
    finally:
        if saved is None:
            del sys.modules["indicator_condition_check"]
        else:
            sys.modules["indicator_condition_check"] = saved
    assert lab.draft_from(raw()).mandate is not None


def test_the_edges_of_the_statement_the_weight_and_the_assumption_ids() -> None:
    assert lab.draft_from(raw(statement="x")).choices == ()
    assert lab.draft_from(raw(max_weight="NaN")).choices == ("the weight is a decimal number, not 'NaN'",)
    assert lab.draft_from(raw(max_weight="Infinity")).choices == ("the weight is a decimal number, not 'Infinity'",)
    for bad_id in ("", 5, None):
        draft = lab.draft_from(raw(assumptions=[{"id": bad_id, "kind": "OBSERVATION", "text": "x"}]))
        assert draft.choices == ("an assumption has an id, a kind and a text",), bad_id
    three = [{"id": "a1", "kind": "OBSERVATION", "text": "x"}] * 3
    assert lab.draft_from(raw(assumptions=three)).choices == ("an assumption id is used once: a1",)
