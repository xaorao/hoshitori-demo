"""Checks of the model arms' settings (``hoshitori.model_settings``), runnable against a mutant."""

from __future__ import annotations

import copy
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hoshitori import arm_l1, arm_l1x, arm_l2

CONFIG: dict[str, Any] = {
    "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "max_tokens": 6000,
    "temperature": "0",
    "seed": None,
    "response_format": "json_schema",
    "timeout_s": 120,
    "thinking": None,
}
SAMPLE: dict[str, Any] = {**CONFIG, "temperature": "0.7"}
GOOD: dict[str, Any] = {
    "name": "nvidia",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "key_env": "LLM_API_KEY",
    "workers": 4,
    "concurrency": 4,
    "rate_per_min": 30,
    "attempt_cap_s": 180,
    "min_attempt_s": 5,
    "l1": {**CONFIG},
    "l2": {"bull": {**CONFIG}, "bear": {**CONFIG}, "judge": {**CONFIG}, "cap_tokens": 100000},
    "l1x": {"sample": {**SAMPLE}, "seed_base": 1000, "max_samples": 15},
}


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
        except self.mod.ModelSettingsError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def text(change: Callable[[dict[str, Any]], None] | None = None) -> str:
    doc = copy.deepcopy(GOOD)
    if change is not None:
        change(doc)
    return json.dumps(doc)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("members", sorted(mod.MEMBERS), sorted(GOOD))
    c.eq("config members", sorted(mod.CONFIG_MEMBERS), sorted(CONFIG))
    c.eq("debate members", sorted(mod.DEBATE_MEMBERS), ["bear", "bull", "cap_tokens", "judge"])
    c.eq("control members", sorted(mod.CONTROL_MEMBERS), ["max_samples", "sample", "seed_base"])
    c.eq("limits", (mod.MAX_CAP_TOKENS, mod.MAX_TIMEOUT_S), (10**7, 600.0))
    c.eq("frozen", mod.ModelSettings.__dataclass_params__.frozen, True)
    return c.bad


def check_good(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = mod.parse(text())
    c.eq(
        "the endpoint",
        (s.name, s.base_url, s.key_env, s.workers, s.concurrency, s.rate_per_min, s.attempt_cap_s, s.min_attempt_s),
        ("nvidia", "https://integrate.api.nvidia.com/v1", "LLM_API_KEY", 4, 4, 30.0, 180.0, 5.0),
    )
    model = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    single = arm_l1.Config(model, 6000, "0", None, "json_schema", 120.0)
    c.eq("the single judgment", s.l1, single)
    c.eq("the debate", s.l2, arm_l2.Config(single, single, single, 100000))
    c.eq("the control", s.l1x, arm_l1x.Config(arm_l1.Config(model, 6000, "0.7", None, "json_schema", 120.0), 1000, 15))
    c.eq(
        "the endpoint object",
        (s.endpoint().name, s.endpoint().url, s.endpoint().provider),
        ("nvidia", "https://integrate.api.nvidia.com/v1", "model-nvidia"),
    )
    c.eq(
        "the versions",
        s.versions(),
        {"L1": arm_l1.agent_version_hash(s.l1), "L2": arm_l2.agent_version_hash(s.l2), "L1x": arm_l1x.agent_version_hash(s.l1x)},
    )
    only = mod.parse(text(lambda d: d.update(l2=None, l1x=None)))
    c.eq("only the single judgment", (only.l2, only.l1x, list(only.versions())), (None, None, ["L1"]))
    c.eq("only the debate", list(mod.parse(text(lambda d: d.update(l1=None, l1x=None))).versions()), ["L2"])
    c.eq(
        "a model that has no settings of its own for the single judgment",
        list(mod.parse(text(lambda d: d.update(l1=None))).versions()),
        ["L2", "L1x"],
    )
    different = mod.parse(
        text(lambda d: d["l1"].update(model="other/model", temperature=None, seed=7, response_format=None, timeout_s=30.5))
    )
    c.eq("other settings", different.l1, arm_l1.Config("other/model", 6000, None, 7, None, 30.5))
    c.eq(
        "a json object format is taken",
        mod.parse(text(lambda d: d["l1"].update(response_format="json_object"))).l1.response_format,
        "json_object",
    )
    c.eq(
        "thinking on, off or left to the provider",
        [mod.parse(text(lambda d, v=v: d["l1"].update(thinking=v))).l1.thinking for v in (True, False, None)],
        [True, False, None],
    )
    c.eq("a temperature of two", mod.parse(text(lambda d: d["l1"].update(temperature="2.0"))).l1.temperature, "2.0")
    c.eq(
        "a seed of zero and the largest",
        [mod.parse(text(lambda d, v=v: d["l1"].update(seed=v))).l1.seed for v in (0, 2**31 - 1)],
        [0, 2**31 - 1],
    )
    c.eq(
        "tokens at both ends", [mod.parse(text(lambda d, v=v: d["l1"].update(max_tokens=v))).l1.max_tokens for v in (1, 65536)], [1, 65536]
    )
    c.eq(
        "the control's smallest and largest",
        [
            (x.l1x.max_samples, x.l1x.seed_base)
            for x in (
                mod.parse(text(lambda d: d["l1x"].update(max_samples=3, seed_base=0))),
                mod.parse(text(lambda d: d["l1x"].update(seed_base=2**31 - 1 - 15))),
            )
        ],
        [(3, 0), (15, 2**31 - 1 - 15)],
    )
    c.eq("requests in flight at both ends", [mod.parse(text(lambda d, v=v: d.update(concurrency=v))).concurrency for v in (1, 64)], [1, 64])
    c.eq("rates", [mod.parse(text(lambda d, v=v: d.update(rate_per_min=v))).rate_per_min for v in (0.5, 6000)], [0.5, 6000.0])
    c.eq("workers at both ends", [mod.parse(text(lambda d, v=v: d.update(workers=v))).workers for v in (1, 64)], [1, 64])
    c.eq(
        "a cap on the debate at both ends",
        [mod.parse(text(lambda d, v=v: d["l2"].update(cap_tokens=v))).l2.cap_tokens for v in (1, 10**7)],
        [1, 10**7],
    )
    c.eq("the least attempt may equal the cap", mod.parse(text(lambda d: d.update(min_attempt_s=180))).min_attempt_s, 180.0)
    c.eq(
        "a timeout of half a second and an attempt of a tenth",
        (
            mod.parse(text(lambda d: d["l1"].update(timeout_s=0.5))).l1.timeout_s,
            mod.parse(text(lambda d: d.update(min_attempt_s=0.1))).min_attempt_s,
        ),
        (0.5, 0.1),
    )
    c.eq("a timeout at the limit", mod.parse(text(lambda d: d["l1"].update(timeout_s=600))).l1.timeout_s, 600.0)
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def refuse(label: str, says: str, change: Callable[[dict[str, Any]], None]) -> None:
        c.refuses(label, lambda: mod.parse(text(change)), says)

    members = f"the model settings has the members {sorted(GOOD)}"
    c.refuses(
        "text that is not JSON",
        lambda: mod.parse("{"),
        "the model settings are not JSON: Expecting property name enclosed in double quotes",
    )
    c.refuses("a list", lambda: mod.parse("[]"), members)
    refuse("a member missing", members, lambda d: d.pop("workers"))
    refuse("a further member", members, lambda d: d.update(extra=1))
    refuse(
        "a key given instead of the name of a variable",
        "key_env is the name of an environment variable, in capitals",
        lambda d: d.update(key_env="nvapi-abc123"),
    )
    refuse(
        "a variable in lower case", "key_env is the name of an environment variable, in capitals", lambda d: d.update(key_env="llm_api_key")
    )
    refuse("a variable that is not text", "key_env is the name of an environment variable, in capitals", lambda d: d.update(key_env=3))
    refuse("a variable that is empty", "key_env is the name of an environment variable, in capitals", lambda d: d.update(key_env=""))
    c.eq("a variable of 64 characters is taken", mod.parse(text(lambda d: d.update(key_env="A" * 64))).key_env, "A" * 64)
    refuse("a variable of 65", "key_env is the name of an environment variable, in capitals", lambda d: d.update(key_env="A" * 65))
    refuse("a name that is not one", "an endpoint's name is 1 to 32 characters of a-z, 0-9, - and _", lambda d: d.update(name="Bad Name"))
    refuse("a name that is not text", "an endpoint's name is 1 to 32 characters of a-z, 0-9, - and _", lambda d: d.update(name=1))
    refuse(
        "a url with a password",
        "an endpoint's URL is an http(s) URL with a host and no credentials, query or fragment",
        lambda d: d.update(base_url="https://u:p@host/v1"),
    )
    refuse(
        "a url that is not text",
        "an endpoint's URL is an http(s) URL with a host and no credentials, query or fragment",
        lambda d: d.update(base_url=None),
    )
    for bad in (0, 65, True, 1.5, "4"):
        refuse(f"{bad!r} workers", "the number of workers is a whole number from 1 to 64", lambda d, bad=bad: d.update(workers=bad))
    for bad in (0, 65, True, 1.5, "4"):
        refuse(
            f"{bad!r} requests in flight",
            "the number of requests in flight is a whole number from 1 to 64",
            lambda d, bad=bad: d.update(concurrency=bad),
        )
    for bad in (0, -1, 6001, True, "x", None):
        refuse(f"a rate of {bad!r}", "the rate is a number above 0 and at most 6000", lambda d, bad=bad: d.update(rate_per_min=bad))
    for bad in (0, -1, 601, True, "x", None):
        refuse(
            f"an attempt cap of {bad!r}",
            "the attempt cap is a number above 0 and at most 600",
            lambda d, bad=bad: d.update(attempt_cap_s=bad),
        )
    for bad in (0, 181, True):
        refuse(
            f"a least attempt of {bad!r}",
            "the least attempt is a number above 0 and at most 180",
            lambda d, bad=bad: d.update(min_attempt_s=bad),
        )
    cfg = "l1 has the members " + str(sorted(CONFIG))
    refuse("a config member missing", cfg, lambda d: d["l1"].pop("seed"))
    refuse("a config that is not an object", cfg, lambda d: d.update(l1="m"))
    refuse("a timeout of nothing", "l1: the timeout is a number above 0 and at most 600", lambda d: d["l1"].update(timeout_s=0))
    refuse("a timeout too long", "l1: the timeout is a number above 0 and at most 600", lambda d: d["l1"].update(timeout_s=601))
    refuse("a timeout that is a flag", "l1: the timeout is a number above 0 and at most 600", lambda d: d["l1"].update(timeout_s=True))
    refuse("a model with a space", "l1: the model is an id of 1 to 100 characters", lambda d: d["l1"].update(model="a b"))
    refuse("a model that is not text", "l1: the model is an id of 1 to 100 characters", lambda d: d["l1"].update(model=7))
    refuse("tokens of none", "l1: max_tokens is a whole number from 1 to 65536", lambda d: d["l1"].update(max_tokens=0))
    refuse("tokens too many", "l1: max_tokens is a whole number from 1 to 65536", lambda d: d["l1"].update(max_tokens=65537))
    refuse(
        "a temperature of three",
        "l1: the temperature is a decimal string from 0 to 2 with at most three places",
        lambda d: d["l1"].update(temperature="3"),
    )
    refuse(
        "a temperature that is a number",
        "l1: the temperature is a decimal string from 0 to 2 with at most three places",
        lambda d: d["l1"].update(temperature=0.5),
    )
    refuse("a seed too large", f"l1: the seed is a whole number from 0 to {2**31 - 1}", lambda d: d["l1"].update(seed=2**31))
    refuse("a negative seed", f"l1: the seed is a whole number from 0 to {2**31 - 1}", lambda d: d["l1"].update(seed=-1))
    refuse(
        "a format that is not one",
        "l1: the response format is null, json_object or json_schema",
        lambda d: d["l1"].update(response_format="xml"),
    )
    for bad in (1, 0, "no"):
        refuse(f"thinking of {bad!r}", "l1: thinking is true or false", lambda d, bad=bad: d["l1"].update(thinking=bad))
    debate = "l2 has the members " + str(sorted(["bull", "bear", "judge", "cap_tokens"]))
    refuse("a debate with no judge", debate, lambda d: d["l2"].pop("judge"))
    refuse("a bad bull", "l2 bull: the model is an id of 1 to 100 characters", lambda d: d["l2"]["bull"].update(model=""))
    refuse("a bad bear", "l2 bear: the model is an id of 1 to 100 characters", lambda d: d["l2"]["bear"].update(model=""))
    refuse("a bad judge", "l2 judge: the model is an id of 1 to 100 characters", lambda d: d["l2"]["judge"].update(model=""))
    for bad in (0, 10**7 + 1, True):
        refuse(
            f"a debate cap of {bad!r}",
            "the debate's token cap is a whole number from 1 to 10000000",
            lambda d, bad=bad: d["l2"].update(cap_tokens=bad),
        )
    control = "l1x has the members " + str(sorted(["sample", "seed_base", "max_samples"]))
    refuse("a control with no sample", control, lambda d: d["l1x"].pop("sample"))
    refuse("a bad sample", "l1x sample: the model is an id of 1 to 100 characters", lambda d: d["l1x"]["sample"].update(model=""))
    refuse(
        "a sample with a seed", "the control sets its own seeds, so its sample carries none", lambda d: d["l1x"]["sample"].update(seed=1)
    )
    refuse(
        "a sample at temperature zero",
        "the control's samples are drawn at a temperature above zero",
        lambda d: d["l1x"]["sample"].update(temperature="0"),
    )
    refuse(
        "a sample at temperature 0.0",
        "the control's samples are drawn at a temperature above zero",
        lambda d: d["l1x"]["sample"].update(temperature="0.0"),
    )
    refuse(
        "a sample with no temperature",
        "the control's samples are drawn at a temperature above zero",
        lambda d: d["l1x"]["sample"].update(temperature=None),
    )
    top_seed = 2**31 - 1 - 15
    for bad in (-1, top_seed + 1, True):
        refuse(
            f"a first seed of {bad!r}",
            f"the control's first seed is a whole number from 0 to {top_seed}",
            lambda d, bad=bad: d["l1x"].update(seed_base=bad),
        )
    for bad in (2, 16, True):
        refuse(
            f"{bad!r} samples at most",
            "the control's most samples is a whole number from 3 to 15",
            lambda d, bad=bad: d["l1x"].update(max_samples=bad),
        )
    refuse("a control with no debate", "the matched-compute control needs the debate it is matched to", lambda d: d.update(l2=None))
    refuse("no arm at all", "there is at least one model arm", lambda d: d.update(l1=None, l2=None, l1x=None))
    return c.bad


def check_load(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    file = root / "models.json"
    file.write_text(text(), encoding="utf-8")
    c.eq("a file is read", mod.load(file), mod.parse(text()))
    c.refuses(
        "a file that is not there", lambda: mod.load(root / "missing.json"), "the model settings cannot be read: No such file or directory"
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    import tempfile

    bad: list[str] = []
    for check in (check_constants, check_good, check_refusals, check_load):
        try:
            if check is check_load:
                with tempfile.TemporaryDirectory() as tmp:
                    found = check(mod, Path(tmp))
            else:
                found = check(mod)
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
