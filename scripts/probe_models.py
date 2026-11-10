"""What does the model endpoint actually do? (the finding is CF-62.)

A measurement, not part of the product: a handful of small requests to a few models of an OpenAI-compatible chat endpoint, to learn what
the transport of the model client must handle (a response format, the shape of an answer, usage with reasoning tokens, error bodies,
latencies, repeatability under a fixed seed). The key is read from the environment file and sent to the endpoint named there; it is never
printed or written. The output file holds only what the endpoint answered to these tiny prompts and is kept outside the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

MODELS = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "deepseek-ai/deepseek-v4.1-flash",
    "moonshotai/kimi-k2.6",
)
SCHEMA = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}, "p": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["symbol", "p"],
    "additionalProperties": False,
}
ASK = 'Give your probability that BTC closes higher in one hour than now, as the JSON object {"symbol":"BTC","p":<number>}. Only the JSON.'


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            out[name.strip()] = value.strip()
    return out


def call(base: str, key: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - the endpoint named in the environment file
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310
            raw = reply.read()
            status, headers = reply.status, dict(reply.headers)
    except urllib.error.HTTPError as exc:
        raw, status, headers = exc.read(), exc.code, dict(exc.headers)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"http": 0, "seconds": round(time.monotonic() - started, 2), "error": type(exc).__name__}
    seconds = round(time.monotonic() - started, 2)
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"http": status, "seconds": seconds, "raw_head": raw[:300].decode("utf-8", "replace")}
    interesting = {k: v for k, v in headers.items() if k.lower().startswith(("x-ratelimit", "retry-after", "x-request", "nvcf"))}
    return {"http": status, "seconds": seconds, "headers": interesting, "body": parsed}


def summary(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    out: dict[str, Any] = {k: v for k, v in result.items() if k != "body"}
    if not isinstance(body, dict):
        return out
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        message = choice.get("message", {})
        out["finish_reason"] = choice.get("finish_reason")
        out["message_keys"] = sorted(message)
        out["content"] = message.get("content")
        for name in ("reasoning_content", "reasoning"):
            if message.get(name) is not None:
                out[f"{name}_chars"] = len(str(message[name]))
    out["usage"] = body.get("usage")
    out["body_keys"] = sorted(body)
    if "choices" not in body:
        out["error_body"] = json.dumps(body)[:400]
    return out


def probe_model(base: str, key: str, model: str, timeout: float) -> dict[str, Any]:
    plain = {"model": model, "messages": [{"role": "user", "content": ASK}], "temperature": 0, "max_tokens": 600}
    cases: dict[str, dict[str, Any]] = {
        "plain": plain,
        "json_object": {**plain, "response_format": {"type": "json_object"}},
        "json_schema": {
            **plain,
            "response_format": {"type": "json_schema", "json_schema": {"name": "forecast", "schema": SCHEMA, "strict": True}},
        },
        "short": {**plain, "max_tokens": 5},
        "seed_a": {**plain, "temperature": 0.7, "seed": 7},
        "seed_b": {**plain, "temperature": 0.7, "seed": 7},
    }
    return {name: summary(call(base, key, body, timeout)) for name, body in cases.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True, help="the environment file with LLM_BASE_URL and LLM_API_KEY")
    parser.add_argument("--out", type=Path, required=True, help="where to write the findings (outside the repository)")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    args = parser.parse_args(argv)
    env = read_env(args.env)
    base, key = env.get("LLM_BASE_URL", ""), env.get("LLM_API_KEY", "")
    if not base or not key:
        print("the environment file has no LLM_BASE_URL or LLM_API_KEY", file=sys.stderr)
        return 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {model: pool.submit(probe_model, base, key, model, args.timeout) for model in args.models}
        results = {model: future.result() for model, future in futures.items()}
    wrong = summary(call(base, "not-a-key", {"model": args.models[0], "messages": [{"role": "user", "content": "x"}]}, 30))
    unknown = summary(call(base, key, {"model": "no/such-model", "messages": [{"role": "user", "content": "x"}]}, 30))
    args.out.write_text(json.dumps({"models": results, "wrong_key": wrong, "unknown_model": unknown}, indent=1), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
