"""The blind forecast arena (contract section 32.2 and 32.2a).

An arena cycle is its own cycle: its own schedule, snapshot and batch commitment, never pooled with a league cycle. People
forecast horizons of a day or more, because a person cannot use the six minutes a one-hour league cycle allows. A participant
commits a digest of the forecast and a salt before the cutoff; nobody sees any forecast before the batch is committed, and a
reveal must match its digest. The window plus the attestation margin and the wait for the next candle close never exceeds the
gap bound of one twenty-fourth of the horizon.

Two rooms keep information honest. In the open-information room a person may have researched anything, so a comparison with an
agent is labelled *same deadline, different information*. In the controlled-evidence room the person received exactly the
cycle's bundle and attests that they used only it; the product cannot enforce that and says so. A comparison is made inside one
room only, and an agent that fetched live evidence is never compared with another class. With fewer than five people the arena
is agent-only and is labelled.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

HORIZONS: Final = (24, 168)
ROOMS: Final = ("OPEN", "CONTROLLED")
JOURNEYS: Final = ("BLIND_ARENA", "REMATCH", "OPEN_CHALLENGE")
INFO_CLASSES: Final = ("AGENT_SNAPSHOT_ONLY", "AGENT_LIVE_ACQUISITION", "HUMAN_OPEN", "HUMAN_CONTROLLED_ATTESTED")
HUMAN_CLASSES: Final = ("HUMAN_OPEN", "HUMAN_CONTROLLED_ATTESTED")
ATTESTATION_MARGIN_S: Final = 120.0
CANDLE_WAIT_S: Final = 60.0
DEFAULT_WINDOWS: Final = {24: 2700.0, 168: 14400.0}  # priors: 45 minutes and four hours
MIN_PEOPLE: Final = 5
COPY_TOLERANCE: Final = 0.02
LABELS: Final = {
    "OPEN": "same deadline, different information",
    "CONTROLLED": "same bundle; the person's compliance is attested, not enforced",
}
AGENT_ONLY: Final = "agent-only: fewer than 5 people took part"
FORBIDDEN: Final = ("same information", "beat the ai", "equal terms")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ArenaError(ValueError):
    """A commitment, a reveal or a comparison that is refused."""


def check_copy(text: str) -> None:
    """The copy of an arena result never says that everyone had the same information or that anyone beat the AI."""
    lowered = text.lower()
    for phrase in FORBIDDEN:
        if phrase in lowered:
            raise ArenaError(f"the copy '{text}' uses a phrase the open-information room forbids: {phrase}")


@dataclass
class _Entry:
    identity: str
    asset: str
    digest: str
    room: str
    info_class: str
    journey: str
    body: dict[str, Any] | None = None


class Arena:
    def __init__(
        self, cycle_id: str, horizon_h: int, *, snapshot_at: float, clock: Callable[[], float], window_s: float | None = None
    ) -> None:
        if horizon_h not in HORIZONS:
            raise ArenaError(f"people forecast 24 hours and longer: {horizon_h} hours is not offered")
        self.cycle_id, self.horizon_h, self.snapshot_at, self._now = cycle_id, horizon_h, snapshot_at, clock
        self.gap_bound_s = horizon_h * 3600 / 24
        self.window_s = DEFAULT_WINDOWS[horizon_h] if window_s is None else window_s
        if not self.window_s > 0:
            raise ArenaError("the window is a positive number of seconds")
        if self.window_s + ATTESTATION_MARGIN_S + CANDLE_WAIT_S > self.gap_bound_s:
            raise ArenaError(
                f"the window of {self.window_s} s plus the attestation margin and the candle wait "
                f"exceeds the gap bound of {self.gap_bound_s} s"
            )
        self.cutoff = snapshot_at + self.window_s
        self.state = "OPEN"
        self.batch_root: str | None = None
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._selection: dict[str, tuple[int, int]] = {}

    # --- commitments ----------------------------------------------------------------------------------------------------------------
    def time_left(self) -> float:
        return max(0.0, self.cutoff - self._now())

    def commitments(self) -> int:
        return len(self._entries)

    def public(self) -> dict[str, Any]:
        """What anyone may see before the batch is committed: counts and time, never an identity, a digest or a forecast."""
        return {
            "cycle": self.cycle_id,
            "horizon_h": self.horizon_h,
            "state": self.state,
            "commitments": self.commitments(),
            "time_left_s": self.time_left(),
        }

    def commit(self, identity: str, asset: str, digest: str, *, room: str, info_class: str, attested: bool, journey: str) -> None:
        if self._now() > self.cutoff or self.state != "OPEN":
            raise ArenaError("the window has closed: no commitment after the cutoff")
        if journey not in JOURNEYS:
            raise ArenaError(f"a journey is one of {list(JOURNEYS)}")
        if journey == "OPEN_CHALLENGE":
            raise ArenaError("an open challenge is late and never enters an arena")
        if room not in ROOMS:
            raise ArenaError(f"a room is one of {list(ROOMS)}")
        if info_class not in INFO_CLASSES:
            raise ArenaError(f"an information class is one of {list(INFO_CLASSES)}")
        if room == "CONTROLLED" and info_class == "HUMAN_OPEN":
            raise ArenaError("the controlled-evidence room takes HUMAN_CONTROLLED_ATTESTED and agents, not HUMAN_OPEN")
        if room == "OPEN" and info_class == "HUMAN_CONTROLLED_ATTESTED":
            raise ArenaError("HUMAN_CONTROLLED_ATTESTED belongs in the controlled-evidence room")
        if info_class == "HUMAN_CONTROLLED_ATTESTED" and not attested:
            raise ArenaError("a controlled-evidence entry carries the participant's attestation")
        if not _DIGEST.fullmatch(digest):
            raise ArenaError("a commitment is a sha-256 digest of 64 hexadecimal characters")
        if (identity, asset) in self._entries:
            raise ArenaError("one active forecast per identity, asset and horizon in a cycle")
        self._entries[(identity, asset)] = _Entry(identity, asset, digest, room, info_class, journey)

    def commit_batch(self) -> str:
        if self.state == "BATCH_COMMITTED":
            raise ArenaError("the batch is committed once")
        if self._now() <= self.cutoff:
            raise ArenaError("the batch is committed after the cutoff")
        digests = sorted(e.digest for e in self._entries.values())
        self.batch_root = hashlib.sha256("".join(digests).encode()).hexdigest()
        self.state = "BATCH_COMMITTED"
        return self.batch_root

    # --- the reveal -----------------------------------------------------------------------------------------------------------------
    def _entry(self, identity: str, asset: str | None = None) -> _Entry:
        found = [e for e in self._entries.values() if e.identity == identity and (asset is None or e.asset == asset)]
        if not found:
            raise ArenaError(f"{identity} has no commitment in this cycle")
        if len(found) > 1:
            raise ArenaError(f"name the asset: {identity} has several commitments")
        return found[0]

    def reveal(self, identity: str, body: bytes, salt: bytes, asset: str | None = None) -> None:
        if self.state != "BATCH_COMMITTED":
            raise ArenaError("a forecast is revealed after the batch is committed")
        entry = self._entry(identity, asset)
        if entry.body is not None:
            raise ArenaError(f"{identity} has already revealed")
        if hashlib.sha256(salt + body).hexdigest() != entry.digest:
            raise ArenaError("the revealed forecast does not match the sealed commitment")
        entry.body = json.loads(body)

    def revealed(self) -> list[dict[str, Any]]:
        if self.state != "BATCH_COMMITTED":
            raise ArenaError("nothing is revealed before the batch is committed")
        agents = [e for e in self._entries.values() if e.info_class.startswith("AGENT") and e.body is not None]
        rows = []
        for e in self._entries.values():
            if e.body is None:
                continue
            same = e.info_class in HUMAN_CLASSES and any(
                a.asset == e.asset and abs(float(a.body["p"]) - float(e.body["p"])) <= COPY_TOLERANCE for a in agents if a.body is not None
            )
            rows.append(
                {
                    "identity": e.identity,
                    "asset": e.asset,
                    "body": e.body,
                    "room": e.room,
                    "info_class": e.info_class,
                    "journey": e.journey,
                    "same_as_agent": same,
                }
            )
        return rows

    # --- labels and comparisons -----------------------------------------------------------------------------------------------------
    def label(self) -> str:
        people = [e for e in self._entries.values() if e.info_class in HUMAN_CLASSES]
        if len(people) < MIN_PEOPLE:
            return AGENT_ONLY
        rooms = [r for r in ROOMS if any(e.room == r for e in people)]  # in the fixed order of the rooms
        return "people took part: " + "; ".join(LABELS[r] for r in rooms)

    @staticmethod
    def compare_labels() -> dict[str, str]:
        return dict(LABELS)

    def compare(self, first: str, second: str) -> str:
        if first == second:
            raise ArenaError("a comparison is between two entries")
        a, b = self._entry(first), self._entry(second)
        if a.room != b.room:
            raise ArenaError("a comparison is made inside one room")
        if "AGENT_LIVE_ACQUISITION" in (a.info_class, b.info_class):
            raise ArenaError("an agent that fetched live evidence is never compared with another class")
        return LABELS[a.room]

    # --- selection ------------------------------------------------------------------------------------------------------------------
    def disclose(self, identity: str, *, entered: int, open_count: int) -> None:
        if entered < 0 or open_count < 0:
            raise ArenaError("the counts are not negative")
        if entered > open_count:
            raise ArenaError("a participant cannot have entered more than were open")
        self._selection[identity] = (entered, open_count)

    def disclosure(self, identity: str) -> str:
        if identity not in self._selection:
            return "no selection record"
        entered, open_count = self._selection[identity]
        return f"entered {entered} of {open_count} open"
