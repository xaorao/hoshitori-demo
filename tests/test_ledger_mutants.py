"""Each rule of the ledger is protected by a scenario that fails when the rule is broken.

Not covered here, on purpose: the ``fsync`` calls. A process that is merely stopped keeps the operating system's cache
intact, so removing an fsync cannot be seen without a power-loss test, which has not been run . Three
guards are redundant with others and have no mutant: the decreasing-size check on checkpoints (a decreasing size with
matching roots is still a valid prefix), the defensive re-scan after a repair, and the journal's torn-line skip. Two more
are covered by the manifest chain: a closed segment that is damaged or missing makes the next segment's manifest line
disagree, so the explicit refusal for closed segments and the continuity check on segment names are second guards.
"""

from __future__ import annotations

import pytest

from hoshitori import ledger
from tests.ledger_checks import failures
from tests.mutation import mutant

MUTATIONS = {
    "damage in the last segment is repaired": (
        "if scan.problem is segment.Problem.DAMAGE:\n            raise IntegrityFailure",
        "if False:\n            raise IntegrityFailure",
    ),
    "an indexed damaged last line is treated as torn": (
        '            if row is not None:\n                raise IntegrityFailure(\n                    f"segment',
        '            if False:\n                raise IntegrityFailure(\n                    f"segment',
    ),
    "a torn tail is not truncated": ("handle.truncate(cut)", "handle.truncate(len(data))"),
    "a checkpoint beyond the records is tolerated": ("if checkpoint.size > self._tree.size:", "if False:"),
    "a checkpoint root mismatch is tolerated": ("if self._tree.root(checkpoint.size) != checkpoint.root:", "if False:"),
    "an attested size beyond the records is tolerated": (
        'if size > self._tree.size:\n                raise IntegrityFailure(f"attested',
        'if False:\n                raise IntegrityFailure(f"attested',
    ),
    "an attested root mismatch is tolerated": ("if self._tree.root(size) != bytes(root):", "if False:"),
    "attestations are not reloaded from their file": ("        self._load_attested()\n", "        pass\n"),
    "attestations are not written to a file": (
        '            _append_durably(self.directory / "attested.jsonl", line)\n            self._db.execute("INSERT OR IGNORE INTO attested',
        '            self._db.execute("INSERT OR IGNORE INTO attested',
    ),
    "a mismatching attestation is accepted": ("if size > self._tree.size or self._tree.root(size) != root:", "if False:"),
    "an index row that disagrees with the segment is tolerated": (
        "if bytes(row[3]) != line.leaf or row[1] != source or row[2] != rec_id:",
        "if False:",
    ),
    "index rows beyond the segment are kept": ("for seq in sorted(s for s in by_seq if s >= len(lines)):", "for seq in []:"),
    "outbox rows are not reopened": (
        "\"UPDATE outbox SET state='pending' WHERE source=? AND rec_id=? AND state='done'\"",
        "\"UPDATE outbox SET state='pending' WHERE 0\"",
    ),
    "journal intents are not replayed": ("if in_ledger is None and in_outbox is None:", "if False:"),
    "journal intents are not written": ('            _append_durably(self.directory / "journal.jsonl", intent)\n', "            pass\n"),
    "a conflict with a committed record is accepted": (
        '\n                    raise ConflictError(f"{source} {rec_id} already exists with different content")\n                return Receipt',
        "\n                    pass\n                return Receipt",
    ),
    "a conflict with a queued record is accepted": (
        '\n                    raise ConflictError(f"{source} {rec_id} is already queued with different content")\n                return Receipt',
        "\n                    pass\n                return Receipt",
    ),
    "a group conflict with a committed record is accepted": (
        '\n                        raise ConflictError(f"{source} {rec_id} already exists with different content")\n                    continue',
        "\n                        pass\n                    continue",
    ),
    "a group conflict with a queued record is accepted": (
        '\n                        raise ConflictError(f"{source} {rec_id} is already queued with different content")\n                    continue',
        "\n                        pass\n                    continue",
    ),
    "committed_at may decrease": ("now = max(now, last)", "pass"),
    "a clock stepping back is tolerated": ("if now < last - CLOCK_TOLERANCE:", "if False:"),
    "committed_at is not stored": ('record["committed_at"] = committed_at', "pass"),
    "submit works in INTEGRITY_FAILURE": (
        "            self._require_ok()\n            envelope.validate_producer_record(record)",
        "            envelope.validate_producer_record(record)",
    ),
    "append works in INTEGRITY_FAILURE": ("            self._require_ok()\n            appended = 0", "            appended = 0"),
    "checkpoint works in INTEGRITY_FAILURE": (
        "            self._require_ok()\n            if self._tree.size == 0:",
        "            if self._tree.size == 0:",
    ),
    "the failure state is not persisted": ('_atomic_write(self.directory / "STATE"', '(lambda *a: None)(self.directory / "STATE"'),
    "the manifest line is not written on rotation": ("        os.write(fd, manifest)\n", "        pass\n"),
    "an interrupted rotation is not cleaned up": ('if len(data) == 0 or b"\\n" not in data:', "if False:"),
    "the writer lock is not taken": ("fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass"),
    "the line is written in one piece before the hook": ("_write_all(self._current_fd, line[:half])", "_write_all(self._current_fd, line)"),
    "a lost command is not recorded": ("                lost.append(receipt)\n", "                continue\n"),
    "a receipt already in the ledger is reported lost": (
        "                if in_ledger is not None or in_outbox is not None:\n                    continue",
        "                if False:\n                    continue",
    ),
    "a replica is trusted without commitments": (
        "        if not commitments:\n            raise LedgerError",
        "        if False:\n            raise LedgerError",
    ),
    "a replica shorter than a commitment is accepted": (
        '            if size > tree.size:\n                raise IntegrityFailure(f"the replica',
        '            if False:\n                raise IntegrityFailure(f"the replica',
    ),
    "a replica that contradicts a commitment is accepted": (
        '            if tree.root(size) != root:\n                raise IntegrityFailure(f"the replica',
        '            if False:\n                raise IntegrityFailure(f"the replica',
    ),
    "damaged segments are deleted, not quarantined": (
        'os.replace(d / "segments", quarantine / f"segments-{number}")',
        'shutil.rmtree(d / "segments")',
    ),
    "the failure flag is not cleared after a restore": (
        '        (d / "STATE").unlink(missing_ok=True)\n        _fsync_dir(d)',
        "        _fsync_dir(d)",
    ),
    "a different root for a signed size is accepted": (
        "                    if bytes(latest[1]) != root:\n                        raise IntegrityFailure",
        "                    if False:\n                        raise IntegrityFailure",
    ),
    "a smaller checkpoint than one already signed is accepted": (
        "                if latest[0] > size:\n                    raise IntegrityFailure",
        "                if False:\n                    raise IntegrityFailure",
    ),
    "the index row is not written": (
        '"INSERT INTO ledger(seq, source, rec_id, leaf, segment, off, len) VALUES(?,?,?,?,?,?,?)",\n                (seq, source',
        '"INSERT INTO ledger(seq, source, rec_id, leaf, segment, off, len) SELECT ?,?,?,?,?,?,? WHERE 0",\n                (seq, source',
    ),
    "another ledger format is accepted": ("    if found != FORMAT:", "    if False:"),
    "a ledger with no FORMAT file is not given one": ('        if not (d / "FORMAT").exists():', "        if False:"),
    "the format is never checked": (
        "        check_format(d)  # before anything is locked or written\n",
        "",
    ),
    "the format file is written without its newline": ('(FORMAT + "\\n").encode("utf-8")', 'FORMAT.encode("utf-8")'),
}


def test_real_module_passes() -> None:
    assert failures(ledger) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.ledger", old, new)), f"mutant not detected: {name}"
