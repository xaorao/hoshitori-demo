"""The batch commitment and the attestation: the checks pass on the real modules, every small change is noticed, and the whole path works
end to end: a ledger, a batch, fake authorities reached through the real gateway, a bundle, and the offline verifier's verdict."""

from __future__ import annotations

from pathlib import Path

from hoshitori import attest, bundle, canonical, commit, verify
from hoshitori.ledger import Ledger
from hoshitori.timeline import to_ms
from hoshitori.trusted_root import parse_root
from hoshitori.tsa import PinnedRoot, TokenPolicy
from tests import root_support as R
from tests.attest_checks import CLOCK, now, world
from tests.commit_checks import failures as commit_failures
from tests.commit_checks import record
from tests.mutation_sweep import survivors


def test_commit_module_passes_every_check() -> None:
    assert commit_failures(commit) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Attestor = Callable[[bytes, int], Attestation]|0": "a type alias that only the type checker reads",
    "attest_appended|True<->False|True|0": "the three lists a zip joins are built from the same keys, so they are always as long as each other",
}


def test_every_change_of_the_commit_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.commit", "tests.commit_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_a_batch_is_stamped_by_authorities_and_the_offline_verifier_agrees(tmp_path: Path) -> None:
    specs = [
        ("alpha", "operator-a", "Org A", None, None),
        ("beta", "operator-b", "Org B", None, None),
        ("gamma", "operator-c", "Org C", None, None),
    ]
    with world(specs) as (runner, store, sites, authorities):
        log_signer = R.log_signer(7)
        document = R.make_document(
            1,
            root_keys=[R.new_signer(1)],
            eras=[{"vkey": log_signer.verifier.vkey(), "from_size": 0, "through_size": None}],
            pinned=[(s.chain.root, s.operator) for s in sites],
        )
        root = parse_root(canonical.dumps(document))  # type: ignore[arg-type]
        policy = TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites))
        ledger = Ledger.open(tmp_path / "ledger", signer=log_signer, origin=R.LOG_ORIGIN, clock=CLOCK, segment_records=100)
        try:

            def attestor(digest: bytes, committed_at_ms: int) -> attest.Attestation:
                return attest.attest(
                    runner, store, CLOCK, authorities, digest, committed_at_ms=committed_at_ms, policy=policy, rules=root.time_stamp
                )

            batch = [record(1, "snapshot"), record(2), record(3), record(4)]
            got = commit.commit_batch(ledger, batch, attestor=attestor, rules=root.time_stamp)
            assert got.uniform and got.level == "C3", [r.level for r in got.records]
            assert got.attestation.origin_ms > got.attestation.eligible_after_ms >= got.attestation.attested_at_ms  # type: ignore[operator]
            assert got.attestation.origin_ms > to_ms(now()) - 1_000 * 3600
            directory = tmp_path / "bundle"
            bundle.export_bundle(directory, ledger, [r.seq for r in got.records], checkpoint_note=got.note, tokens=got.attestation.tokens())
            verdict = verify.verify_bundle(directory, root)
            assert verdict["integrity"]["ok"], verdict["integrity"]["errors"]
            assert verdict["timing"]["level"] == "C3", verdict["timing"]
            assert sorted(verdict["integrity"]["items"]) == [f"item-{r.seq:08d}" for r in got.records]
        finally:
            ledger.close()
