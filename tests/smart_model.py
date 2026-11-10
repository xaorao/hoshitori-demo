"""A model endpoint on loopback that answers like a model would: from the prompt it is given, in the shape its stage asks for.

It reads the ids the prompt offers as citable, and answers with the first of them. The single judgment and the judge give a call at
a fixed probability with no claim about the numbers; a debater argues for its side; the Desk's proposer takes a small long stance. It keeps every request it saw so a check can look at
exactly what was sent, and it can be told to stay silent (a 503) for a stage.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tests.model_world import FakeModel, ok
from tests.ryo_fake_server import Behaviour, Seen

IDS = re.compile(r"ev[a-z2-7]{26}")


class SmartModel(FakeModel):
    def __init__(self, *, p: float = 0.62) -> None:
        super().__init__()
        self.p = p
        self.down: set[str] = set()  # stages (the names of the response schemas) the endpoint answers with a 503

    def default(self, seen: Seen) -> Behaviour:
        if seen.method != "POST" or seen.path != "/v1/chat/completions" or seen.headers.get("authorization") != f"Bearer {self.key}":
            return super().default(seen)
        body = json.loads(seen.body)
        with self._lock:
            self.requests.append(body)
        stage = body["response_format"]["json_schema"]["name"]
        if stage in self.down:
            return Behaviour(503, b'{"error":"unavailable"}', {"content-type": "application/json"})
        text = "\n".join(m["content"] for m in body["messages"])
        ident = sorted(set(IDS.findall(text)))[0]
        if stage == "desk_propose":  # the Desk's proposer: a long stance at the endpoint's probability, a small weight, one citation
            return ok(json.dumps({"stance": "LONG", "p": self.p, "weight": 0.05, "cites": [ident], "summary": "the move held"}))
        if stage in ("l2_bull", "l2_bear"):
            answer: dict[str, Any] = {
                "claim": f"{stage} says up" if stage == "l2_bull" else f"{stage} says down",
                "support": [ident] if stage == "l2_bull" else [],
                "oppose": [] if stage == "l2_bull" else [ident],
                "uncertainty": "thin data",
                "would_change_if": "a close below the last",
            }
        else:
            answer = {
                "decision": "CALL",
                "p": self.p,
                "invalidations": [{"field": "last_close", "op": "below", "level": 1}],
                "cites": [ident],
                "assertions": [],
                "summary": f"seed {body.get('seed')}",
            }
        return ok(json.dumps(answer))
