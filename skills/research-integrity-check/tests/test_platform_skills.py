"""The platform's skill shape served beside the builder routes: list, one definition, invoke, over real HTTP."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

from research_integrity_check import platform_skills, schema, server


def _http(url: str, body: Any = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        with err:
            return err.code, json.loads(err.read())


def test_the_platform_routes() -> None:
    srv = server.serve_http("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        status, listing = _http(base + "/api/skills/")
        assert status == 200 and [s["name"] for s in listing] == [t["name"] for t in schema.TOOL_DEFINITIONS]
        for entry in listing:
            assert set(entry) == {"name", "description", "args", "requires_guard", "xp"}
            assert all(a["type"] in ("string", "number", "boolean", "object", "array") for a in entry["args"])
        assert _http(base + "/api/skills/nope")[0] == 404
        name = schema.TOOL_NAME
        status, out = _http(base + f"/api/skills/{name}/invoke", {"name": name, "args": schema.EXAMPLE_CALL})
        assert status == 200 and out["status"] == "success" and out["result"]["tool"] == name
        status, out = _http(base + f"/api/skills/{name}/invoke", {"name": name, "args": {}})
        assert status == 200 and out["status"] == "error"
        assert _http(base + f"/api/skills/{name}/invoke", {"name": name})[0] == 422
    finally:
        srv.shutdown()
        srv.server_close()


def test_definition_of_every_tool() -> None:
    for tool in schema.TOOL_DEFINITIONS:
        d = platform_skills.definition(tool)
        assert d["name"] == tool["name"] and d["requires_guard"] is False and d["xp"] == 0
