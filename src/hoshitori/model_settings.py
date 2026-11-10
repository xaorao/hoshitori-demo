"""The settings of the model arms: which endpoint, which key (by the name of the environment variable that holds it, never the key), and
how each arm asks (``CYCLE_RUNNER_SPEC`` section 4, ``AGENT_PROTOCOL`` section 3).

The file is read strictly: every member once, every value in the range the endpoint's own adapter accepts, so a setting that would make
every call fail is refused when the league is made and not at the first cycle. The agent version of each arm is computed from these
settings, and the protocol pins it; a league whose settings drift from the pinned versions does not start.

* **The control samples at a temperature above zero** and sets its own seeds, so it carries none.
* **The key is never in the file.** ``key_env`` names the variable; the deployment reads it when the league starts and fails, naming the
  variable and not its value, if it is empty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from hoshitori import arm_l1, arm_l1x, arm_l2
from hoshitori.connector import ConnectorError
from hoshitori.model_gateway import MAX_SEED, Endpoint, ModelAdapter, ModelConfigError

MEMBERS: Final = frozenset(
    {"name", "base_url", "key_env", "workers", "concurrency", "rate_per_min", "attempt_cap_s", "min_attempt_s", "l1", "l2", "l1x"}
)
MAX_RATE: Final = 6000.0
CONFIG_MEMBERS: Final = frozenset({"model", "max_tokens", "temperature", "seed", "response_format", "timeout_s", "thinking"})
DEBATE_MEMBERS: Final = frozenset({"bull", "bear", "judge", "cap_tokens"})
CONTROL_MEMBERS: Final = frozenset({"sample", "seed_base", "max_samples"})
MAX_CAP_TOKENS: Final = 10**7
MAX_TIMEOUT_S: Final = 600.0
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class ModelSettingsError(ValueError):
    """Settings of the model arms that cannot be used."""


@dataclass(frozen=True)
class ModelSettings:
    name: str
    base_url: str
    key_env: str
    workers: int  # candidates and horizons decided at the same time
    concurrency: int  # requests in flight to the endpoint at once
    rate_per_min: float  # requests the endpoint is asked for in a minute
    attempt_cap_s: float
    min_attempt_s: float
    l1: arm_l1.Config | None
    l2: arm_l2.Config | None
    l1x: arm_l1x.Config | None

    def endpoint(self) -> Endpoint:
        return Endpoint(self.name, self.base_url)

    def versions(self) -> dict[str, str]:
        """The agent version hash of each configured arm."""
        out: dict[str, str] = {}
        if self.l1 is not None:
            out["L1"] = arm_l1.agent_version_hash(self.l1)
        if self.l2 is not None:
            out["L2"] = arm_l2.agent_version_hash(self.l2)
        if self.l1x is not None:
            out["L1x"] = arm_l1x.agent_version_hash(self.l1x)
        return out


def _object(value: object, names: frozenset[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != names:
        raise ModelSettingsError(f"{what} has the members {sorted(names)}")
    return value


def _number(value: object, what: str, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= high:
        raise ModelSettingsError(f"{what} is a number above 0 and at most {high:g}")
    return float(value)


def _whole(value: object, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ModelSettingsError(f"{what} is a whole number from {low} to {high}")
    return value


def _config(value: object, what: str, adapter: ModelAdapter) -> arm_l1.Config:
    entry = _object(value, CONFIG_MEMBERS, what)
    config = arm_l1.Config(
        entry["model"],
        entry["max_tokens"],
        entry["temperature"],
        entry["seed"],
        entry["response_format"],
        _number(entry["timeout_s"], f"{what}: the timeout", MAX_TIMEOUT_S),
        entry["thinking"],
    )
    args: dict[str, Any] = {"model": config.model, "messages": [{"role": "user", "content": "x"}], "max_tokens": config.max_tokens}
    if config.temperature is not None:
        args["temperature"] = config.temperature
    if config.seed is not None:
        args["seed"] = config.seed
    if config.thinking is not None:
        args["thinking"] = config.thinking
    if config.response_format not in (None, "json_object", "json_schema"):
        raise ModelSettingsError(f"{what}: the response format is null, json_object or json_schema")
    try:
        adapter.normalise("chat", args)
    except ConnectorError as exc:
        raise ModelSettingsError(f"{what}: {exc.message}") from exc
    return config


def parse(text: str) -> ModelSettings:
    """Settings of the model arms from the JSON text of a file; every member once, every value checked."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelSettingsError(f"the model settings are not JSON: {exc.msg}") from exc
    top = _object(raw, MEMBERS, "the model settings")
    name, url, key_env = top["name"], top["base_url"], top["key_env"]
    if not isinstance(key_env, str) or _ENV.fullmatch(key_env) is None:
        raise ModelSettingsError("key_env is the name of an environment variable, in capitals")
    try:
        endpoint = Endpoint(name if isinstance(name, str) else "", url if isinstance(url, str) else "")
    except ModelConfigError as exc:
        raise ModelSettingsError(str(exc)) from exc
    workers = _whole(top["workers"], "the number of workers", 1, 64)
    concurrency = _whole(top["concurrency"], "the number of requests in flight", 1, 64)
    rate = _number(top["rate_per_min"], "the rate", MAX_RATE)
    cap = _number(top["attempt_cap_s"], "the attempt cap", MAX_TIMEOUT_S)
    least = _number(top["min_attempt_s"], "the least attempt", cap)
    adapter = ModelAdapter(endpoint, attempt_cap_s=cap, min_attempt_s=least)
    l1 = None if top["l1"] is None else _config(top["l1"], "l1", adapter)
    l2 = None
    if top["l2"] is not None:
        entry = _object(top["l2"], DEBATE_MEMBERS, "l2")
        l2 = arm_l2.Config(
            _config(entry["bull"], "l2 bull", adapter),
            _config(entry["bear"], "l2 bear", adapter),
            _config(entry["judge"], "l2 judge", adapter),
            _whole(entry["cap_tokens"], "the debate's token cap", 1, MAX_CAP_TOKENS),
        )
    l1x = None
    if top["l1x"] is not None:
        entry = _object(top["l1x"], CONTROL_MEMBERS, "l1x")
        sample = _config(entry["sample"], "l1x sample", adapter)
        if sample.seed is not None:
            raise ModelSettingsError("the control sets its own seeds, so its sample carries none")
        if sample.temperature is None or Decimal(sample.temperature) <= 0:
            raise ModelSettingsError("the control's samples are drawn at a temperature above zero")
        l1x = arm_l1x.Config(
            sample,
            _whole(entry["seed_base"], "the control's first seed", 0, MAX_SEED - arm_l1x.MAX_SAMPLES),
            _whole(entry["max_samples"], "the control's most samples", arm_l1x.FIRST, arm_l1x.MAX_SAMPLES),
        )
    if l1 is None and l2 is None and l1x is None:
        raise ModelSettingsError("there is at least one model arm")
    if l1x is not None and l2 is None:
        raise ModelSettingsError("the matched-compute control needs the debate it is matched to")
    return ModelSettings(endpoint.name, endpoint.url, key_env, workers, concurrency, rate, cap, least, l1, l2, l1x)


def load(file: Path) -> ModelSettings:
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelSettingsError(f"the model settings cannot be read: {exc.strerror}") from exc
    return parse(text)
