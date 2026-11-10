"""E21: single-writer safety across failover (an outside review 1). A discrete model of a primary, a replica, an external compare-and-swap cell (standing in for
a linearizable conditional write in object storage or a coordination service) and witnesses that hold published checkpoints.
Protocols:
  FILELOCK  what ARCHITECTURE.md said: a lock protects processes on one machine; a promoted replica simply starts writing.
  FENCED    promotion is a manual operator action that (1) increments an epoch with a compare-and-swap, (2) requires the replica to prove continuity from the last
            externally attested checkpoint with an RFC 9162 consistency proof, else it fails closed, and (3) a checkpoint is released (published, time-stamped,
            handed to witnesses) only if a compare-and-swap that carries the writer's epoch and the previous pointer succeeds.
Every checkpoint is signed with the SAME key on both machines (the reused-key case): the signature alone never distinguishes the histories. A fork is two released
checkpoints that are not consistent with each other. 'LEAK' adds a misbehaving old primary that releases a locally signed checkpoint without the publication step.

Added for review 21: EPOCH records. A promotion writes an epoch record into the log and the cell: epoch e closes at size `closing[e]` (the continuity checkpoint the new writer proved).
A verifier ACCEPTS a checkpoint only if it is consistent with every published checkpoint AND, when signed under an epoch-e key, its size does not exceed closing[e]. 'Reused key' is
modelled as the old primary signing with its epoch-0 key after the promotion. SIMULTANEOUS promotion: a second candidate that read the epoch before the first promotion tries the same
compare-and-swap.
Not product code."""
import hashlib, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "E2a"))
import merkle as M


class Cell:
    def __init__(self): self.epoch, self.version, self.pointer = 0, 0, (0, M.mth([]))       # pointer = last published checkpoint (size, root)
    def cas_epoch(self, expect, new):
        if self.epoch != expect: return False
        self.epoch, self.version = new, self.version + 1; return True
    def publish(self, epoch, expect_pointer, new_pointer):
        if self.epoch != epoch or self.pointer != expect_pointer: return False
        self.pointer = new_pointer; self.version += 1; return True


class Node:
    def __init__(self, name): self.name, self.leaves, self.epoch, self.fenced_off, self.released = name, [], 0, False, []
    def append(self, rec): self.leaves.append(M.leaf_hash(rec))
    def checkpoint(self): n = len(self.leaves); return (n, M.mth(self.leaves[:n]))


def consistent(logs, c1, c2):
    """Two checkpoints are consistent iff the operator holding the larger one can prove the smaller is its prefix."""
    (n1, r1), (n2, r2) = sorted([c1, c2])
    if n1 == n2: return r1 == r2
    for lg in logs:
        if len(lg) >= n2 and M.mth(lg[:n2]) == r2:
            return M.verify_consistency(n1, n2, M.consistency_proof(n1, lg[:n2]), r1, r2) if n1 > 0 else True
    return False        # nobody can prove it: the larger checkpoint's own log does not exist among the logs we have


def run(protocol, rng, steps=40, lag_ok=True, leak=False):
    cell, A, B = Cell(), Node("A"), Node("B")
    A.epoch = 0; promoted, partitioned_a, rid = False, False, 0
    released, stats = [], {"promotion_refused": 0, "released_checkpoints": 0, "leak_releases": 0, "simultaneous_second_refused": 0, "simultaneous_second_won": 0, "leak_rejected_by_epoch_rule": 0, "leak_accepted_harmless": 0}
    closing = {}
    replica_synced = 0                      # how many of A's leaves B has copied (the replica lags)
    def a_checkpoint():
        nonlocal released
        if A.fenced_off and protocol == "FENCED": return
        cp = A.checkpoint()
        if protocol == "FENCED":
            if partitioned_a: return                                  # cannot reach the cell: cannot publish
            if not cell.publish(A.epoch, cell.pointer, cp): A.fenced_off = True; return
        released.append(("A", A.epoch, cp)); stats["released_checkpoints"] += 1
    def b_checkpoint():
        cp = B.checkpoint()
        if protocol == "FENCED" and not cell.publish(B.epoch, cell.pointer, cp): return
        released.append(("B", B.epoch, cp)); stats["released_checkpoints"] += 1
    for _ in range(steps):
        ev = rng.choice(["a_append", "a_append", "a_checkpoint", "sync", "partition", "heal", "promote", "b_append", "b_checkpoint", "leak"])
        if ev == "a_append" and not (A.fenced_off and protocol == "FENCED"): rid += 1; A.append(b"A%d" % rid)
        elif ev == "a_checkpoint": a_checkpoint()
        elif ev == "sync":
            if not partitioned_a: replica_synced = len(A.leaves); B.leaves = list(A.leaves[:replica_synced]) if not promoted else B.leaves
        elif ev == "partition": partitioned_a = True
        elif ev == "heal": partitioned_a = False
        elif ev == "promote" and not promoted:
            if protocol == "FILELOCK":
                promoted = True; B.epoch = 0
                if rng.random() < 0.5: stats["simultaneous_second_won"] += 1          # a second replica started its own writer under the file-lock design
            else:
                ptr = cell.pointer
                ok_prefix = len(B.leaves) >= ptr[0] and (ptr[0] == 0 or M.mth(B.leaves[:ptr[0]]) == ptr[1])     # continuity from the last attested checkpoint
                seen_epoch = cell.epoch
                if ok_prefix and cell.cas_epoch(cell.epoch, cell.epoch + 1):
                    promoted, B.epoch = True, cell.epoch; closing[seen_epoch] = ptr[0]
                    if rng.random() < 0.5:                         # a second candidate that read the same epoch tries the same swap
                        if cell.cas_epoch(seen_epoch, seen_epoch + 1): stats["simultaneous_second_won"] += 1
                        else: stats["simultaneous_second_refused"] += 1
                else: stats["promotion_refused"] += 1
        elif ev == "b_append" and promoted: rid += 1; B.append(b"B%d" % rid)
        elif ev == "b_checkpoint" and promoted: b_checkpoint()
        elif ev == "leak" and leak and promoted:
            released.append(("A-leak", A.epoch, A.checkpoint())); stats["leak_releases"] += 1
    logs = [A.leaves, B.leaves]
    cps = [r[2] for r in released]
    fork = any(not consistent(logs, cps[i], cps[j]) for i in range(len(cps)) for j in range(i + 1, len(cps)))
    published = [r for r in released if r[0] != "A-leak"]
    fork_published = any(not consistent(logs, published[i][2], published[j][2]) for i in range(len(published)) for j in range(i + 1, len(published)))
    if protocol == "FENCED":
        accepted = [r for r in released if all(consistent(logs, r[2], q[2]) for q in published) and r[2][0] <= closing.get(r[1], 10 ** 9)]
    else:
        accepted = list(released)                                  # the file-lock design has no published chain or epoch rule to test against
    acps = [r[2] for r in accepted]
    fork_accepted = any(not consistent(logs, acps[i], acps[j]) for i in range(len(acps)) for j in range(i + 1, len(acps)))
    for r in released:
        if r[0] == "A-leak":
            stats["leak_accepted_harmless" if r in accepted else "leak_rejected_by_epoch_rule"] += 1
    stats["fork_accepted"] = fork_accepted
    return fork, fork_published, stats, promoted


if __name__ == "__main__":
    import json
    out = {}
    for protocol in ("FILELOCK", "FENCED"):
        for leak in (False, True):
            rng = random.Random(21); n = 1500; forks = fp = refused = promoted_n = fa = sec_ref = sec_won = lk_rej = lk_ok = 0
            for _ in range(n):
                f, fpub, st, pr = run(protocol, rng, leak=leak)
                forks += f; fp += fpub; refused += st["promotion_refused"]; promoted_n += pr; fa += st["fork_accepted"]
                sec_ref += st["simultaneous_second_refused"]; sec_won += st["simultaneous_second_won"]; lk_rej += st["leak_rejected_by_epoch_rule"]; lk_ok += st["leak_accepted_harmless"]
            out[f"{protocol}{'+leak' if leak else ''}"] = {"schedules": n, "schedules_with_a_fork_among_all_released_checkpoints": forks, "schedules_with_a_fork_among_PUBLISHED_checkpoints": fp,
                "schedules_with_a_fork_among_ACCEPTED_checkpoints": fa, "promotions_refused": refused, "schedules_where_the_replica_became_writer": promoted_n,
                "simultaneous_second_promotions_won": sec_won, "simultaneous_second_promotions_refused": sec_ref, "stale_primary_checkpoints_rejected": lk_rej if protocol == "FENCED" else "not applicable: the file-lock design has no acceptance rule", "stale_primary_checkpoints_accepted_as_harmless_prefix": lk_ok if protocol == "FENCED" else "not applicable: the file-lock design has no acceptance rule"}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E21_results.json"), "w"), indent=1); print(json.dumps(out, indent=1))
