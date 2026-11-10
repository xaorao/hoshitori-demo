"""Checks of the model call record (``hoshitori.model_call_record``), runnable against a mutant."""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical, envelope, model_client
from hoshitori.ids import natural_id
from hoshitori.model_client import ModelCall, Outcome, Usage

SOURCE = "urn:hoshitori:league:test"
DIGEST = "ab" * 32
OTHER = "cd" * 32
CAPTURE = "cap-" + "1" * 32
WHEN = 1_793_358_000_000
REPORTED = "the reported model is an id of 1 to 200 characters of valid canonical text"
FINISH = "the finish reason is valid canonical text of 1 to 64 characters"
DETAIL = "the detail is valid canonical text of 1 to 200 characters"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.ModelCallRecordError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def call(**over: Any) -> ModelCall:
    values: dict[str, Any] = {
        "stage": "judge",
        "prompt_version": "v1",
        "request_digest": DIGEST,
        "args_digest": DIGEST,
        "capture_id": CAPTURE,
        "response_digest": OTHER,
        "model": "nvidia/nemotron",
        "reported_model": "nvidia/nemotron",
        "max_tokens": 500,
        "temperature": "0.2",
        "seed": 7,
        "response_format": "json_schema",
        "attempt": 1,
        "usage": Usage(40, 20, 12, 8, 60, False),
        "latency_ms": 1500,
        "finish_reason": "stop",
        "reasoning_chars": 90,
        "outcome": Outcome.VALID,
        "detail": None,
    }
    values.update(over)
    return ModelCall(**values)


def nothing(**over: Any) -> ModelCall:
    """A call that reached nothing."""
    return call(
        capture_id=None,
        response_digest=None,
        reported_model=None,
        usage=None,
        latency_ms=None,
        finish_reason=None,
        reasoning_chars=0,
        outcome=Outcome.UNAVAILABLE,
        detail="CALL_FAILED: down",
        **over,
    )


def record(mod: types.ModuleType, c: ModelCall | None = None) -> dict[str, Any]:
    return mod.to_record(c or call(), SOURCE, WHEN)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.TYPE, mod.TYPE_VERSION, mod.MAX_TOKENS, mod.FORMATS),
        ("model_call", 1, 65_536, (None, "json_object", "json_schema")),
    )
    c.eq("limits", (mod.MAX_INSTANT_MS, mod.MAX_COUNT), (253_402_300_799_999, 10**12))
    return c.bad


def check_roundtrip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, original in (
        ("a call that was read", call()),
        ("a call that reached nothing", nothing()),
        (
            "a call with no optional member",
            call(
                reported_model=None,
                temperature=None,
                seed=None,
                response_format=None,
                finish_reason=None,
                usage=Usage(None, None, None, None, 321, True),
            ),
        ),
        ("a repair", call(attempt=2, outcome=Outcome.INVALID, detail="$.p: is missing")),
        (
            "an unreadable answer",
            call(
                usage=None,
                outcome=Outcome.UNREADABLE,
                detail="not a chat answer",
                finish_reason=None,
                reported_model=None,
                reasoning_chars=0,
            ),
        ),
        ("a seed of zero and a temperature of two", call(seed=0, temperature="2.0")),
        ("a model asked to think", call(thinking=True)),
        ("a model asked not to think", call(thinking=False)),
    ):
        rec = record(mod, original)
        envelope.validate_producer_record(rec)
        back = mod.from_record(canonical.parse_record(canonical.dumps(rec)))
        c.eq(f"{label} reads back whole", back, original)
    rec = record(mod)
    c.eq(
        "type, source, privacy, time and refs",
        (rec["type"], rec["type_version"], rec["source"], rec["privacy"], rec["created_at"], "refs" in rec),
        ("model_call", 1, SOURCE, "public", "2026-10-30T11:00:00.000Z", False),
    )
    c.eq("the body holds no text of a prompt or an answer", sorted(rec["body"]), sorted(mod._MEMBERS))
    c.eq("a read call is identified by its capture", rec["id"], natural_id("mc", {"capture": CAPTURE}))
    c.eq("the same capture at another time is the same record", mod.to_record(call(), SOURCE, WHEN + 5)["id"], rec["id"])
    gone = nothing()
    c.eq(
        "a call that reached nothing is identified by its arguments, attempt, outcome and moment",
        mod.to_record(gone, SOURCE, WHEN)["id"],
        natural_id("mc", {"args": DIGEST, "attempt": 1, "outcome": "UNAVAILABLE", "at_ms": WHEN}),
    )
    ids = {
        mod.to_record(gone, SOURCE, WHEN)["id"],
        mod.to_record(gone, SOURCE, WHEN + 1)["id"],
        mod.to_record(nothing(attempt=2), SOURCE, WHEN)["id"],
        mod.to_record(dataclasses.replace(gone, outcome=Outcome.BUDGET), SOURCE, WHEN)["id"],
        mod.to_record(dataclasses.replace(gone, args_digest=OTHER), SOURCE, WHEN)["id"],
    }
    c.eq("another moment, attempt, outcome or request is another id", len(ids), 5)
    c.eq(
        "the body of a usage",
        rec["body"]["usage"],
        {"prompt_tokens": 40, "completion_tokens": 20, "reasoning_tokens": 12, "cached_tokens": 8, "billed": 60, "estimated": False},
    )
    c.eq("no usage is null", record(mod, nothing())["body"]["usage"], None)
    return c.bad


def check_call_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    form = " is not in the form this record takes"
    for label, over, says in (
        ("a stage in capitals", {"stage": "Judge"}, "the stage" + form),
        ("a stage that is empty", {"stage": ""}, "the stage" + form),
        ("a stage that is null", {"stage": None}, "the stage" + form),
        ("a version that is null", {"prompt_version": None}, "the prompt version" + form),
        ("a request digest that is null", {"request_digest": None}, "the request digest" + form),
        ("an arguments digest that is null", {"args_digest": None}, "the arguments digest" + form),
        ("a version with a space", {"prompt_version": "v 1"}, "the prompt version" + form),
        ("a request digest in capitals", {"request_digest": "AB" * 32}, "the request digest" + form),
        ("a request digest that is short", {"request_digest": "ab" * 31}, "the request digest" + form),
        ("an arguments digest that is not text", {"args_digest": 5}, "the arguments digest" + form),
        ("a capture id that is not one", {"capture_id": "cap-1"}, "the capture id" + form),
        ("a response digest that is not one", {"response_digest": "x"}, "the response digest" + form),
        ("a temperature of 2.1", {"temperature": "2.1"}, "the temperature" + form),
        ("a temperature that is a number", {"temperature": 0.5}, "the temperature" + form),
        (
            "a capture with no digest",
            {"response_digest": None},
            "a call that has a stored answer names it by id and by digest, and one that has none names neither",
        ),
        (
            "a digest with no capture",
            {"capture_id": None},
            "a call that has a stored answer names it by id and by digest, and one that has none names neither",
        ),
        ("an empty model", {"model": ""}, "the model is an id of 1 to 100 characters"),
        ("a model of 101 characters", {"model": "m" * 101}, "the model is an id of 1 to 100 characters"),
        ("a reported model that is empty", {"reported_model": ""}, REPORTED),
        ("a reported model of 201 characters", {"reported_model": "m" * 201}, REPORTED),
        ("a reported model that is not text", {"reported_model": 5}, REPORTED),
        ("a reported model that is not NFC", {"reported_model": "mode\u0301l"}, REPORTED),
        ("a reported model with a lone surrogate", {"reported_model": "m\ud800"}, REPORTED),
        ("a reported model with a code point assigned after Unicode 15.0", {"reported_model": "m\U0001fae9"}, REPORTED),
        ("a ceiling of zero", {"max_tokens": 0}, "the token ceiling is a whole number from 1 to 65536"),
        ("a ceiling above the limit", {"max_tokens": 65_537}, "the token ceiling is a whole number from 1 to 65536"),
        ("a ceiling that is a boolean", {"max_tokens": True}, "the token ceiling is a whole number from 1 to 65536"),
        ("a seed below zero", {"seed": -1}, "the seed is a whole number from 0 to 2147483647"),
        ("a seed above the limit", {"seed": 2**31}, "the seed is a whole number from 0 to 2147483647"),
        ("a format that does not exist", {"response_format": "xml"}, "the response format is none, json_object or json_schema"),
        ("thinking that is a number", {"thinking": 1}, "thinking is true, false or left to the provider"),
        ("thinking that is text", {"thinking": "no"}, "thinking is true, false or left to the provider"),
        ("attempt zero", {"attempt": 0}, "the attempt is a whole number from 1 to 2"),
        ("attempt three", {"attempt": 3}, "the attempt is a whole number from 1 to 2"),
        ("a negative latency", {"latency_ms": -1}, "the latency is a whole number from 0 to 1000000000"),
        ("a latency above the limit", {"latency_ms": 10**9 + 1}, "the latency is a whole number from 0 to 1000000000"),
        ("an empty finish reason", {"finish_reason": ""}, FINISH),
        ("a finish reason of 65 characters", {"finish_reason": "f" * 65}, FINISH),
        ("a finish reason that is not text", {"finish_reason": 7}, FINISH),
        ("a finish reason that is not NFC", {"finish_reason": "sto\u0301p"}, FINISH),
        ("a finish reason with a code point assigned after Unicode 15.0", {"finish_reason": "stop\U0001fae9"}, FINISH),
        ("a negative reasoning length", {"reasoning_chars": -1}, "the length of the reasoning is a whole number from 0 to 1000000000"),
        ("an outcome that is text", {"outcome": "VALID"}, "the outcome is one of the outcomes"),
        ("an empty detail", {"detail": ""}, DETAIL),
        ("a detail of 201 characters", {"detail": "d" * 201}, DETAIL),
        ("a detail that is not text", {"detail": 5}, DETAIL),
        ("a detail that is not NFC", {"detail": "$.note\u0301: is not a member of this answer"}, DETAIL),
        ("a detail with a lone surrogate", {"detail": "d\ud800"}, DETAIL),
        ("a detail with a code point assigned after Unicode 15.0", {"detail": "d\U0001fae9"}, DETAIL),
        (
            "negative prompt tokens",
            {"usage": Usage(-1, 20, None, None, 19, False)},
            "the prompt tokens is a whole number from 0 to 1000000000000",
        ),
        (
            "completion tokens that are text",
            {"usage": Usage(40, "20", None, None, 60, False)},
            "the completion tokens is a whole number from 0 to 1000000000000",
        ),
        (
            "negative reasoning tokens",
            {"usage": Usage(40, 20, -1, None, 60, False)},
            "the reasoning tokens is a whole number from 0 to 1000000000000",
        ),
        (
            "cached tokens above the limit",
            {"usage": Usage(40, 20, None, 10**12 + 1, 60, False)},
            "the cached tokens is a whole number from 0 to 1000000000000",
        ),
        (
            "billed tokens negative",
            {"usage": Usage(40, 20, None, None, -1, False)},
            "the billed tokens is a whole number from 0 to 1000000000000",
        ),
        (
            "an estimate flag that is not a boolean",
            {"usage": Usage(40, 20, None, None, 60, 0)},
            "usage is an estimate exactly when the provider did not give both counts",
        ),
        (
            "counts marked as an estimate",
            {"usage": Usage(40, 20, None, None, 60, True)},
            "usage is an estimate exactly when the provider did not give both counts",
        ),
        (
            "an estimate with both counts missing marked as exact",
            {"usage": Usage(None, None, None, None, 60, False)},
            "usage is an estimate exactly when the provider did not give both counts",
        ),
        (
            "a sum that is not the sum",
            {"usage": Usage(40, 20, None, None, 61, False)},
            "billed tokens are the prompt count plus the completion count",
        ),
        (
            "usage on a call that read nothing",
            {"capture_id": None, "response_digest": None},
            "a call has usage exactly when its answer was read",
        ),
        ("no usage on a call that read an answer", {"usage": None}, "a call has usage exactly when its answer was read"),
        ("usage on an unreadable answer", {"outcome": Outcome.UNREADABLE}, "a call has usage exactly when its answer was read"),
    ):
        c.refuses(label, lambda over=over: mod.to_record(call(**over), SOURCE, WHEN), says)
    for _label, over in (
        ("a model of one character", {"model": "m"}),
        ("a reported model of one character", {"reported_model": "m"}),
        ("a reported model with a code point assigned in Unicode 15.0", {"reported_model": "m\U0001fae8"}),
        ("a reported model in NFC", {"reported_model": "mod\u00e8l"}),
        ("a finish reason of one character", {"finish_reason": "s"}),
        ("a detail of one character", {"detail": "d"}),
        ("a detail in NFC", {"detail": "$.not\u00e9e: is not a member of this answer"}),
        ("a ceiling of one", {"max_tokens": 1}),
        ("a ceiling at the limit", {"max_tokens": 65_536}),
        ("a model of 100 characters", {"model": "m" * 100}),
        ("a reported model of 200 characters", {"reported_model": "m" * 200}),
        ("a seed at the limit", {"seed": 2**31 - 1}),
        ("a latency of zero and at the limit", {"latency_ms": 0}),
        ("a detail of 200 characters", {"detail": "d" * 200}),
        ("a finish reason of 64 characters", {"finish_reason": "f" * 64}),
        ("tokens at the limit", {"usage": Usage(10**12, 0, None, 10**12, 10**12, False)}),
        ("an estimate with half the counts", {"usage": Usage(40, None, None, None, 99, True)}),
        ("an unreadable answer with no usage", {"usage": None, "outcome": Outcome.UNREADABLE}),
    ):
        mod.to_record(call(**over), SOURCE, WHEN)
    mod.to_record(call(latency_ms=10**9), SOURCE, WHEN)
    c.eq("the boundaries are accepted", True, True)
    for label, moment in (("before the epoch", -1), ("after the year 9999", 253_402_300_800_000), ("a boolean", True)):
        c.refuses(
            f"a time {label}",
            lambda moment=moment: mod.to_record(call(), SOURCE, moment),
            "the creation time is a whole number of milliseconds",
        )
    c.eq("the last instant is allowed", mod.to_record(call(), SOURCE, 253_402_300_799_999)["created_at"], "9999-12-31T23:59:59.999Z")
    c.eq("and time zero", mod.to_record(call(), SOURCE, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(call(), "league", WHEN),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    return c.bad


def check_reader(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = record(mod)

    def tampered(change: Callable[[dict[str, Any]], None], source: dict[str, Any] | None = None) -> dict[str, Any]:
        copy = canonical.parse_record(canonical.dumps(source or good))
        change(copy)  # type: ignore[arg-type]
        return copy  # type: ignore[return-value]

    stamped = {**good, "committed_at": "2026-10-30T11:00:01.000Z"}
    c.eq("a record as the ledger returns it reads the same", mod.from_record(stamped), call())
    c.refuses(
        "another type",
        lambda: mod.from_record(tampered(lambda r: r.update(type="practice"))),
        "not a model call record of a version this reader knows",
    )
    c.refuses(
        "another version",
        lambda: mod.from_record(tampered(lambda r: r.update(type_version=2))),
        "not a model call record of a version this reader knows",
    )
    c.refuses(
        "a broken envelope",
        lambda: mod.from_record(tampered(lambda r: r.update(spec="x"))),
        "the envelope: spec must be 'hoshitori.record/1'",
    )
    c.refuses(
        "a member short",
        lambda: mod.from_record(tampered(lambda r: r["body"].pop("stage"))),
        f"the body has the members {sorted(mod._MEMBERS)}",
    )
    c.refuses(
        "a member too many",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(prompt="text"))),
        f"the body has the members {sorted(mod._MEMBERS)}",
    )
    c.refuses(
        "an outcome that is not one",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(outcome="FINE"))),
        "the outcome is one of the outcomes",
    )
    c.refuses(
        "usage that is not an object",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(usage=[]))),
        "usage has the members of a usage record",
    )
    c.refuses(
        "usage with a member short",
        lambda: mod.from_record(tampered(lambda r: r["body"]["usage"].pop("billed"))),
        "usage has the members of a usage record",
    )
    c.refuses(
        "usage with a flag that is not a boolean",
        lambda: mod.from_record(tampered(lambda r: r["body"]["usage"].update(estimated="no"))),
        "usage says whether it is an estimate",
    )
    c.refuses(
        "a count that is text",
        lambda: mod.from_record(tampered(lambda r: r["body"]["usage"].update(prompt_tokens="40"))),
        "the prompt tokens is a whole number from 0 to 1000000000000",
    )
    for name, what in (
        ("completion_tokens", "the completion tokens"),
        ("reasoning_tokens", "the reasoning tokens"),
        ("cached_tokens", "the cached tokens"),
        ("billed", "the billed tokens"),
    ):
        c.refuses(
            f"{what} that are text",
            lambda name=name: mod.from_record(tampered(lambda r: r["body"]["usage"].update({name: "1"}))),
            f"{what} is a whole number from 0 to 1000000000000",
        )
    c.refuses(
        "a stage the writer would refuse",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(stage="Judge"))),
        "the stage is not in the form this record takes",
    )
    c.refuses(
        "an id that is not the content's",
        lambda: mod.from_record(tampered(lambda r: r.update(id="mcAAAAAAAAAAAAAAAAAAAAAAAAAA"))),
        "the id is not the id of what the record says",
    )
    c.refuses(
        "a capture changed under the same id",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(capture_id="cap-" + "2" * 32))),
        "the id is not the id of what the record says",
    )
    gone = record(mod, nothing())
    c.eq("a call that reached nothing reads back with its own moment", mod.from_record(gone), nothing())
    c.refuses(
        "and not with another moment",
        lambda: mod.from_record(tampered(lambda r: r.update(created_at="2026-10-30T11:00:00.001Z"), gone)),
        "the id is not the id of what the record says",
    )
    return c.bad


def check_one_rule(mod: types.ModuleType) -> list[str]:
    """the text a call holds is checked by the model client's own rule, so an answer the client reads is one this record takes."""
    c = Case(mod)
    c.eq(
        "the rule and its words are the model client's",
        (
            getattr(mod, "held", None) is model_client.held,
            getattr(mod, "REPORTED_MODEL", None) is model_client.REPORTED_MODEL,
            getattr(mod, "FINISH_REASON", None) is model_client.FINISH_REASON,
        ),
        (True, True, True),
    )
    c.eq(
        "and so is the limit of a usage count, which the client reads an answer's usage by",
        getattr(mod, "MAX_COUNT", None) is getattr(model_client, "MAX_COUNT", None),
        True,
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_roundtrip, check_call_refusals, check_reader, check_one_rule):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
