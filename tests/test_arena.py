"""The blind forecast arena (contract section 32.2 and 32.2a): sealed commitments before a cutoff, one batch, a reveal that must match, rooms and
information classes that are never mixed, and an honest label on every comparison."""

from __future__ import annotations

import hashlib
import json

import pytest

from hoshitori import arena

HOUR = 3600.0


class Clock:
    def __init__(self) -> None:
        self.t = 10_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make(horizon_h: int = 24, window_s: float | None = None, now: float = 10_000.0) -> tuple[arena.Arena, Clock]:
    clock = Clock()
    clock.t = now
    return arena.Arena("cy1", horizon_h, snapshot_at=now, clock=clock, window_s=window_s), clock


def forecast(p: float = 0.62, asset: str = "SOL") -> bytes:
    return json.dumps({"asset": asset, "p": p}, sort_keys=True, separators=(",", ":")).encode()


def seal(salt: bytes, body: bytes) -> str:
    return hashlib.sha256(salt + body).hexdigest()


def refuses(call: object, says: str) -> None:
    with pytest.raises(arena.ArenaError) as raised:
        call()  # type: ignore[operator]
    assert str(raised.value) == says


def commit(
    a: arena.Arena, who: str, *, p: float = 0.62, room: str = "OPEN", info: str = "HUMAN_OPEN", asset: str = "SOL", attested: bool = False
) -> bytes:
    salt = who.encode() + b"-salt-" + b"x" * 16
    body = forecast(p, asset)
    a.commit(who, asset, seal(salt, body), room=room, info_class=info, attested=attested, journey="BLIND_ARENA")
    return salt


def test_people_forecast_only_horizons_of_a_day_or_more_and_the_window_fits_the_gap_bound() -> None:
    refuses(lambda: make(1), "people forecast 24 hours and longer: 1 hours is not offered")
    refuses(lambda: make(4), "people forecast 24 hours and longer: 4 hours is not offered")
    a, _ = make(24)
    b, _ = make(168)
    assert (a.window_s, a.gap_bound_s, b.window_s, b.gap_bound_s) == (2700.0, 3600.0, 14400.0, 25200.0)
    refuses(
        lambda: make(24, 3500.0), "the window of 3500.0 s plus the attestation margin and the candle wait exceeds the gap bound of 3600.0 s"
    )
    make(24, 3420.0)  # exactly the bound minus 120 s and 60 s is allowed
    refuses(
        lambda: make(24, 3421.0), "the window of 3421.0 s plus the attestation margin and the candle wait exceeds the gap bound of 3600.0 s"
    )
    refuses(lambda: make(24, 0.0), "the window is a positive number of seconds")


def test_a_commitment_is_a_digest_before_the_cutoff_and_nothing_else_is_shown() -> None:
    a, clock = make()
    commit(a, "alice")
    assert a.state == "OPEN" and a.commitments() == 1
    refuses(lambda: a.revealed(), "nothing is revealed before the batch is committed")
    assert a.public() == {"cycle": "cy1", "horizon_h": 24, "state": "OPEN", "commitments": 1, "time_left_s": 2700.0}
    clock.advance(a.window_s)
    commit(a, "bob")  # exactly at the cutoff is still inside the window
    clock.advance(0.001)
    refuses(lambda: commit(a, "carol"), "the window has closed: no commitment after the cutoff")


def test_one_forecast_per_identity_asset_and_horizon_and_the_digest_must_be_a_sha256() -> None:
    a, _ = make()
    commit(a, "alice")
    refuses(lambda: commit(a, "alice"), "one active forecast per identity, asset and horizon in a cycle")
    commit(a, "alice", asset="ETH")  # another asset is another forecast
    refuses(
        lambda: a.commit("bob", "SOL", "not-a-digest", room="OPEN", info_class="HUMAN_OPEN", attested=False, journey="BLIND_ARENA"),
        "a commitment is a sha-256 digest of 64 hexadecimal characters",
    )
    refuses(
        lambda: a.commit("bob", "SOL", "A" * 64, room="OPEN", info_class="HUMAN_OPEN", attested=False, journey="BLIND_ARENA"),
        "a commitment is a sha-256 digest of 64 hexadecimal characters",
    )


def test_rooms_and_information_classes_are_checked_at_commitment() -> None:
    a, _ = make()
    refuses(lambda: commit(a, "x", room="LOUNGE"), f"a room is one of {list(arena.ROOMS)}")
    refuses(lambda: commit(a, "x", info="HUMAN_PSYCHIC"), f"an information class is one of {list(arena.INFO_CLASSES)}")
    refuses(
        lambda: commit(a, "x", room="CONTROLLED", info="HUMAN_OPEN"),
        "the controlled-evidence room takes HUMAN_CONTROLLED_ATTESTED and agents, not HUMAN_OPEN",
    )
    refuses(
        lambda: commit(a, "x", room="OPEN", info="HUMAN_CONTROLLED_ATTESTED", attested=True),
        "HUMAN_CONTROLLED_ATTESTED belongs in the controlled-evidence room",
    )
    refuses(
        lambda: commit(a, "x", room="CONTROLLED", info="HUMAN_CONTROLLED_ATTESTED", attested=False),
        "a controlled-evidence entry carries the participant's attestation",
    )
    commit(a, "x", room="CONTROLLED", info="HUMAN_CONTROLLED_ATTESTED", attested=True)
    commit(a, "agent1", room="CONTROLLED", info="AGENT_SNAPSHOT_ONLY")
    commit(a, "agent2", room="OPEN", info="AGENT_SNAPSHOT_ONLY")
    refuses(
        lambda: a.commit("h", "SOL", "a" * 64, room="OPEN", info_class="HUMAN_OPEN", attested=False, journey="OPEN_CHALLENGE"),
        "an open challenge is late and never enters an arena",
    )
    refuses(
        lambda: a.commit("h", "SOL", "a" * 64, room="OPEN", info_class="HUMAN_OPEN", attested=False, journey="FREESTYLE"),
        f"a journey is one of {list(arena.JOURNEYS)}",
    )


def test_the_batch_is_committed_once_after_the_cutoff_and_its_root_covers_every_commitment() -> None:
    a, clock = make()
    refuses(lambda: a.commit_batch(), "the batch is committed after the cutoff")
    salts = {w: commit(a, w, p=0.5 + i / 100) for i, w in enumerate(("alice", "bob", "carol"))}
    clock.advance(a.window_s + 1)
    root = a.commit_batch()
    digests = sorted(seal(salts[w], forecast(0.5 + i / 100)) for i, w in enumerate(("alice", "bob", "carol")))
    assert root == hashlib.sha256("".join(digests).encode()).hexdigest() and a.state == "BATCH_COMMITTED" and a.batch_root == root
    refuses(lambda: a.commit_batch(), "the batch is committed once")
    refuses(lambda: commit(a, "dave"), "the window has closed: no commitment after the cutoff")


def test_a_reveal_must_match_the_sealed_digest_and_waits_for_the_batch() -> None:
    a, clock = make()
    salt = commit(a, "alice")
    refuses(lambda: a.reveal("alice", forecast(), salt), "a forecast is revealed after the batch is committed")
    clock.advance(a.window_s + 1)
    a.commit_batch()
    refuses(lambda: a.reveal("alice", forecast(0.99), salt), "the revealed forecast does not match the sealed commitment")
    refuses(lambda: a.reveal("alice", forecast(), b"wrong-salt" * 2), "the revealed forecast does not match the sealed commitment")
    refuses(lambda: a.reveal("nobody", forecast(), salt), "nobody has no commitment in this cycle")
    a.reveal("alice", forecast(), salt)
    refuses(lambda: a.reveal("alice", forecast(), salt), "alice has already revealed")
    assert a.revealed() == [
        {
            "identity": "alice",
            "asset": "SOL",
            "body": json.loads(forecast()),
            "room": "OPEN",
            "info_class": "HUMAN_OPEN",
            "journey": "BLIND_ARENA",
            "same_as_agent": False,
        }
    ]


def test_a_forecast_within_the_tolerance_of_an_agent_s_is_labelled_the_same_as_the_agent() -> None:
    a, clock = make()
    s1 = commit(a, "agent1", p=0.60, info="AGENT_SNAPSHOT_ONLY")
    s2 = commit(a, "alice", p=0.61)
    s3 = commit(a, "bob", p=0.70)
    clock.advance(a.window_s + 1)
    a.commit_batch()
    a.reveal("agent1", forecast(0.60), s1)
    a.reveal("alice", forecast(0.61), s2)
    a.reveal("bob", forecast(0.70), s3)
    flags = {r["identity"]: r["same_as_agent"] for r in a.revealed()}
    assert flags == {"agent1": False, "alice": True, "bob": False}
    assert arena.COPY_TOLERANCE == 0.02


def test_with_fewer_than_five_people_the_arena_is_agent_only_and_says_so() -> None:
    a, clock = make()
    for who in ("a1", "a2", "a3", "a4"):
        commit(a, who)
    commit(a, "agent1", info="AGENT_SNAPSHOT_ONLY")
    clock.advance(a.window_s + 1)
    a.commit_batch()
    assert a.label() == "agent-only: fewer than 5 people took part"
    b, bclock = make()
    for who in ("a1", "a2", "a3", "a4", "a5"):
        commit(b, who)
    bclock.advance(b.window_s + 1)
    b.commit_batch()
    assert b.label() == "people took part: same deadline, different information"
    c, cclock = make()
    for who in ("a1", "a2", "a3", "a4", "a5"):
        commit(c, who, room="CONTROLLED", info="HUMAN_CONTROLLED_ATTESTED", attested=True)
    cclock.advance(c.window_s + 1)
    c.commit_batch()
    assert c.label() == "people took part: same bundle; the person's compliance is attested, not enforced"
    assert arena.MIN_PEOPLE == 5


def test_a_comparison_is_made_only_inside_one_room_and_never_with_a_live_acquisition_agent() -> None:
    a, clock = make()
    commit(a, "alice", info="HUMAN_OPEN")
    commit(a, "agent1", info="AGENT_SNAPSHOT_ONLY")
    commit(a, "live1", info="AGENT_LIVE_ACQUISITION")
    commit(a, "carol", room="CONTROLLED", info="HUMAN_CONTROLLED_ATTESTED", attested=True)
    commit(a, "agent2", room="CONTROLLED", info="AGENT_SNAPSHOT_ONLY")
    clock.advance(a.window_s + 1)
    a.commit_batch()
    assert a.compare("alice", "agent1") == "same deadline, different information"
    assert a.compare("carol", "agent2") == "same bundle; the person's compliance is attested, not enforced"
    refuses(lambda: a.compare("alice", "agent2"), "a comparison is made inside one room")
    refuses(lambda: a.compare("alice", "live1"), "an agent that fetched live evidence is never compared with another class")
    refuses(lambda: a.compare("live1", "agent1"), "an agent that fetched live evidence is never compared with another class")
    refuses(lambda: a.compare("alice", "alice"), "a comparison is between two entries")
    refuses(lambda: a.compare("alice", "zz"), "zz has no commitment in this cycle")
    other = make()[0]
    assert other.compare_labels() == {
        "OPEN": "same deadline, different information",
        "CONTROLLED": "same bundle; the person's compliance is attested, not enforced",
    }


def test_no_label_says_everyone_had_the_same_information_or_that_anyone_beat_the_ai() -> None:
    for text in (arena.LABELS["OPEN"], arena.LABELS["CONTROLLED"], arena.AGENT_ONLY, *arena.LABELS.values()):
        lowered = text.lower()
        assert "same information" not in lowered and "beat the ai" not in lowered and "equal terms" not in lowered
    assert arena.FORBIDDEN == ("same information", "beat the ai", "equal terms")
    arena.check_copy("People took part: same deadline, different information")
    refuses(
        lambda: arena.check_copy("You beat the AI on equal terms"),
        "the copy 'You beat the AI on equal terms' uses a phrase the open-information room forbids: beat the ai",
    )
    refuses(
        lambda: arena.check_copy("everyone had the SAME INFORMATION"),
        "the copy 'everyone had the SAME INFORMATION' uses a phrase the open-information room forbids: same information",
    )


def test_selection_is_disclosed_for_every_participant() -> None:
    a, _ = make()
    a.disclose("alice", entered=3, open_count=40)
    assert a.disclosure("alice") == "entered 3 of 40 open"
    assert a.disclosure("nobody") == "no selection record"
    refuses(lambda: a.disclose("bob", entered=5, open_count=4), "a participant cannot have entered more than were open")
    refuses(lambda: a.disclose("bob", entered=-1, open_count=4), "the counts are not negative")


def test_the_window_is_shown_with_the_time_left_and_the_cycle_numbers_are_exact() -> None:
    a, clock = make()
    assert a.time_left() == 2700.0
    clock.advance(1000)
    assert a.time_left() == 1700.0
    clock.advance(5000)
    assert a.time_left() == 0.0
    assert a.cutoff == 12_700.0 and a.snapshot_at == 10_000.0 and a.horizon_h == 24
    assert (arena.ATTESTATION_MARGIN_S, arena.CANDLE_WAIT_S) == (120.0, 60.0) and arena.HORIZONS == (24, 168)
    assert arena.ROOMS == ("OPEN", "CONTROLLED") and arena.JOURNEYS == ("BLIND_ARENA", "REMATCH", "OPEN_CHALLENGE")
    assert arena.INFO_CLASSES == ("AGENT_SNAPSHOT_ONLY", "AGENT_LIVE_ACQUISITION", "HUMAN_OPEN", "HUMAN_CONTROLLED_ATTESTED")


def test_the_edges_of_the_window_the_batch_and_the_selection_counts() -> None:
    make(24, 0.5)  # any positive window within the bound is allowed
    a, clock = make()
    commit(a, "alice")
    clock.advance(a.window_s)
    refuses(lambda: a.commit_batch(), "the batch is committed after the cutoff")  # exactly at the cutoff is still inside the window
    clock.advance(0.5)
    a.commit_batch()
    a.disclose("zero", entered=0, open_count=0)
    a.disclose("all", entered=4, open_count=4)
    assert a.disclosure("zero") == "entered 0 of 0 open" and a.disclosure("all") == "entered 4 of 4 open"
    refuses(lambda: a.disclose("x", entered=0, open_count=-1), "the counts are not negative")


def test_the_label_names_each_room_with_people_in_its_fixed_order() -> None:
    a, clock = make()
    for who in ("c1", "c2"):
        commit(a, who, room="CONTROLLED", info="HUMAN_CONTROLLED_ATTESTED", attested=True)
    for who in ("o1", "o2", "o3"):
        commit(a, who)
    clock.advance(a.window_s + 1)
    a.commit_batch()
    assert (
        a.label()
        == "people took part: same deadline, different information; same bundle; the person's compliance is attested, not enforced"
    )


def test_only_the_revealed_forecasts_are_listed_and_a_person_with_two_assets_names_the_asset() -> None:
    a, clock = make()
    agent = commit(a, "agent1", info="AGENT_SNAPSHOT_ONLY")
    sol = commit(a, "alice", p=0.9)
    eth_salt = b"alice-salt-" + b"x" * 16
    a.commit(
        "alice", "ETH", seal(eth_salt, forecast(0.3, "ETH")), room="OPEN", info_class="HUMAN_OPEN", attested=False, journey="BLIND_ARENA"
    )
    clock.advance(a.window_s + 1)
    a.commit_batch()
    a.reveal("agent1", forecast(), agent)
    refuses(lambda: a.reveal("alice", forecast(0.9), sol), "name the asset: alice has several commitments")
    assert [r["identity"] for r in a.revealed()] == ["agent1"]  # a person who has not revealed is not listed
    a.reveal("alice", forecast(0.9), sol, asset="SOL")
    a.reveal("alice", forecast(0.3, "ETH"), eth_salt, asset="ETH")
    rows = {(r["identity"], r["asset"]): r["same_as_agent"] for r in a.revealed()}
    assert rows == {("agent1", "SOL"): False, ("alice", "SOL"): False, ("alice", "ETH"): False}
    refuses(lambda: a.reveal("alice", forecast(), sol, asset="XRP"), "alice has no commitment in this cycle")
