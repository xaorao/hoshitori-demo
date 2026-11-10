"""The bundle verifier over its case table, and the rule behind every part of it shown by a mutant."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshitori import verify
from tests.mutation import mutant
from tests.verify_checks import Case, build_cases, build_special_cases, failures

# Guards without a mutant, stated rather than hidden:
# * the proof file size check in ``_load_item`` (``parse_proof`` refuses an oversized proof with the same error code);
# * ``item.canonical_ok`` inside ``item_ok`` (a record that is not canonical also fails its inclusion proof, and the first
#   failure is the one reported);
# * either one of the two limits of ``read_bundle_file``: the size check on the opened descriptor and the read that stops at
#   ``limit + 1`` bytes each refuse the same file with the same code, reading at most ``limit + 1`` bytes, when the other is gone
#   (the bounded read is there for a file that grows after it was opened, which no case here can be).
MUTATIONS = {
    # reading items
    "canonical form is not required": (
        "item.canonical_ok = canonical.dumps(item.record) == item.canonical_bytes",
        "item.canonical_ok = True",
    ),
    "a non-canonical record is not reported": (
        '    if not item.canonical_ok:\n        _fail(item, "CANONICAL", "not in canonical form")',
        "    pass",
    ),
    "the envelope is not validated": ("        envelope.validate_committed_record(item.record)\n", "        pass\n"),
    "a publication file that is not JSON is trusted": (
        '            claims = -1\n    return {\n        "claimed": claims, "authenticated_offline": 0,',
        '            claims = 0\n    return {\n        "claimed": claims, "authenticated_offline": 0,',
    ),
    "NaN is accepted in a publication file": ("json.loads(text, parse_constant=_reject_constant)", "json.loads(text)"),
    "an invalid window is ignored": (
        '        if item.window_opens_at is None:\n            _fail(item, "WINDOW"',
        '        if False:\n            _fail(item, "WINDOW"',
    ),
    "a missing proof is reported as another failure": ('_fail(item, "MISSING_PROOF"', '_fail(item, "PROOF"'),
    "an unreadable proof is ignored": (
        '    except ProofError as exc:\n        _fail(item, "PROOF", str(exc))\n        return item',
        "    except ProofError:\n        return item",
    ),
    "an unreadable checkpoint is ignored": (
        '    except NoteError as exc:\n        _fail(item, "CHECKPOINT", str(exc))\n        return\n    if authenticated',
        "    except NoteError:\n        return\n    if authenticated",
    ),
    "a record file that cannot be read is ignored": (
        '        _fail(item, "UNREADABLE", str(exc))\n        return item',
        "        return item",
    ),
    # bundle files that are not regular files, or too large
    "a bundle file need not be a regular file": (
        "        if not stat.S_ISREG(info.st_mode):\n            raise NotRegularFile(NOT_REGULAR)\n",
        "",
    ),
    "a bundle file is opened so that a FIFO blocks": (
        'os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0))',
        "os.open(path, os.O_RDONLY)",
    ),
    "a record file of any size is read": ("read_bundle_file(item_path, MAX_ITEM_BYTES)", "read_bundle_file(item_path, 2**62)"),
    "a token file of any size is read": ("read_bundle_file(tsa_dir / name, MAX_TOKEN_BYTES)", "read_bundle_file(tsa_dir / name, 2**62)"),
    "publication.json of any size is read": ("read_bundle_file(path, MAX_PUBLICATION_BYTES)", "read_bundle_file(path, 2**62)"),
    "a file of exactly the limit is refused": ("        if info.st_size > limit:", "        if info.st_size >= limit:"),
    "a token over the limit is called unreadable": (
        'evidence.append(timing.TokenEvidence(label, None, "SIZE"))',
        'evidence.append(timing.TokenEvidence(label, None, "UNREADABLE"))',
    ),
    "a refused record file is called too large": (
        '    except (OSError, NotRegularFile) as exc:\n        _fail(item, "UNREADABLE", str(exc))',
        '    except (OSError, NotRegularFile) as exc:\n        _fail(item, "TOO_LARGE", str(exc))',
    ),
    # the order of a record's own times
    "the creation time is not read": ('        item.created_at = _to_the_millisecond(parse_rfc3339(str(item.record["created_at"])))\n', ""),
    "the creation time is not cut to the millisecond": (
        '_to_the_millisecond(parse_rfc3339(str(item.record["created_at"])))',
        'parse_rfc3339(str(item.record["created_at"]))',
    ),
    "the order of a record's times is not described": (
        '"detail": None if result.error is None else timing.order_detail(rules),',
        '"detail": None,',
    ),
    # the standing list of what is not proven
    "the list leaves out the authorities": (
        '    "both authorities honest at once: the level assumes that the time-stamp authorities did not collude or share a time source",\n',
        "",
    ),
    "a bundle without a root does not say where it may come from": (
        "[*NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] if state == STATE_ASSERTED else list(NOT_PROVEN)",
        "list(NOT_PROVEN)",
    ),
    "every bundle says it may not come from Hoshitori": (
        "[*NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] if state == STATE_ASSERTED else list(NOT_PROVEN)",
        "[*NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT]",
    ),
    # a runtime older than Unicode 15.0 (finding D-NFC); the parser refuses on such a runtime too, so the case without records shows the call
    "the verifier does not check the runtime": ("    canonical.require_runtime_unicode()\n    if root is None:", "    if root is None:"),
    # signatures and eras
    "any era key may sign at any size": ("            if root.era_check(era.key, candidate.size) != ERA_ACCEPT:", "            if False:"),
    "a failed signature is called an era failure": ('failure = "SIGNATURE"', 'failure = "ERA"'),
    "the signature is believed without checking": (
        "                candidate = verify_checkpoint(proof.note, [era.key], root.origin)",
        "                candidate = parse_text(text)",
    ),
    "the origin is not compared": (
        "verify_checkpoint(proof.note, [era.key], root.origin)",
        "verify_checkpoint(proof.note, [era.key], parse_text(text).origin)",
    ),
    "an unsigned bundle counts as signed": ('signature: str = "unauthenticated"', 'signature: str = "verified"'),
    "inclusion is not checked": (
        "    item.inclusion = merkle.verify_inclusion(leaf, proof.index, cp.size, list(proof.path), cp.root)",
        "    item.inclusion = True",
    ),
    "inclusion uses the proof's own size": (
        "merkle.verify_inclusion(leaf, proof.index, cp.size, list(proof.path), cp.root)",
        "merkle.verify_inclusion(leaf, proof.index, proof.index + 1 + len(proof.path), list(proof.path), cp.root)",
    ),
    # the bundle as a whole
    "one checkpoint is not required": (
        "    if len(texts) == 1 and whole:\n",
        "    if texts:\n",
    ),
    "differing checkpoints are not reported": (
        '        return _Groups(items, [], None, None, None, "none", ["CHECKPOINTS_DIFFER"])',
        '        return _Groups(items, [], None, None, None, "none", [])',
    ),
    "a bad item name is accepted": ('                errors.append("BAD_STEM")', "                stems.append(stem)"),
    "an empty bundle is fine": ('        errors.append("NO_ITEMS")\n    return stems, errors', "        pass\n    return stems, errors"),
    "a missing items directory is fine": ('        return [], ["NO_ITEMS"]', "        return [], []"),
    "too many items are all read": ("    if len(stems) > MAX_ITEMS:", "    if False:"),
    "too many items are not reported": (
        '        return stems[:MAX_ITEMS], [*errors, "TOO_MANY_ITEMS"]',
        "        return stems[:MAX_ITEMS], errors",
    ),
    "structure errors do not matter": ('and not raw["mismatched"] and not structural', 'and not raw["mismatched"]'),
    "reference mismatches do not matter": ('not refs["mismatched"] and not raw', "not raw"),
    "raw mismatches do not matter": ('and not raw["mismatched"] and not structural', "and not structural"),
    "integrity ok does not need a trusted root": ('            "ok": consistent and authenticated,', '            "ok": consistent,'),
    # root states
    "an expired root is treated as asserted": (
        '    elif root_status == "EXPIRED":\n        state = STATE_EXPIRED',
        "    elif False:\n        state = STATE_EXPIRED",
    ),
    "a root whose update failed is still trusted": (
        "        state = STATE_ASSERTED\n        root = None",
        "        state = STATE_ASSERTED",
    ),
    "an expired root is called current": ("        state = STATE_EXPIRED", "        state = STATE_PINNED"),
    # references and raw payloads
    "reference hashes are not compared": ("            elif digest in by_id[rid]:", "            elif True:"),
    "unresolved references are not listed": (
        "            if rid not in by_id:\n                unresolved.append(label)",
        "            if rid not in by_id:\n                pass",
    ),
    "resolved references are not counted": ("                resolved += 1", "                pass"),
    "raw payloads are not hashed": (
        "path.is_file() and path.stat().st_size <= MAX_RAW_BYTES and hashlib.sha256(path.read_bytes()).hexdigest() == digest",
        "path.is_file() and path.stat().st_size <= MAX_RAW_BYTES",
    ),
    "a directory counts as a raw payload": ("return path.is_file() and path.stat()", "return path.stat()"),
    "a raw payload may be larger than the limit": ("path.stat().st_size <= MAX_RAW_BYTES and", "True and"),
    "missing raw payloads are not listed": (
        "        if path is None:\n            missing.append(digest)",
        "        if path is None:\n            pass",
    ),
    # the barrier record agrees with itself
    "a barrier outcome may be any word": (
        'if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or _price(body["p0"]) is None or body["outcome_price"] is not None:',
        'if _price(body["p0"]) is None or body["outcome_price"] is not None:',
    ),
    "a barrier record may have no first price": (
        'if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or _price(body["p0"]) is None or body["outcome_price"] is not None:',
        'if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or body["outcome_price"] is not None:',
    ),
    "a barrier record may have an outcome price": (
        'if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or _price(body["p0"]) is None or body["outcome_price"] is not None:',
        'if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or _price(body["p0"]) is None:',
    ),
    "a timeout may name a touch": ("        return touch is None\n", "        return True\n"),
    "a touch minute need not be a whole minute": (
        "if not isinstance(touch, int) or isinstance(touch, bool) or touch % 60_000 != 0:",
        "if not isinstance(touch, int) or isinstance(touch, bool):",
    ),
    "a touch may be before the window": (
        "    return origin_at <= touch < origin_at + horizon\n",
        "    return touch < origin_at + horizon\n",
    ),
    "a touch may be at the end of the window": (
        "    return origin_at <= touch < origin_at + horizon\n",
        "    return origin_at <= touch <= origin_at + horizon\n",
    ),
    "stray raw files are not checked": ("        if name not in cited and not intact(present[name], name):", "        if False:"),
    "unreferenced raw files are not listed": (
        '"unreferenced": sorted((name for name in present if name not in cited), key=_u16),',
        '"unreferenced": [],',
    ),
    # the later part: a second checkpoint and what the records after it show
    "a missing consistency file is tolerated": (
        '    if state == "missing":\n        return _Groups(stamped, later, small_text, small, large, "missing", ["CHECKPOINTS_DIFFER"])\n',
        "",
    ),
    "a consistency proof that fails is believed": (
        "            and merkle.verify_consistency(small.size, large.size, path, small.root, large.root)",
        "            and True",
    ),
    "the sizes in the consistency file are not compared": (
        '            and document["from_size"] == small.size\n            and document["to_size"] == large.size\n',
        "",
    ),
    "a consistency file that is not canonical is read": ("        if canonical.dumps(document) != raw:", "        if False:"),
    "a consistency file with another member is read": ("        set(document) != members\n        or ", "        False\n        or "),
    "a consistency file of another format is read": ('        or document.get("format") != layout.CONSISTENCY_FORMAT\n', ""),
    "a path hash may be any text": (
        "        or not all(isinstance(h, str) and _HASH.fullmatch(h) is not None for h in hashes)\n",
        "        or not all(isinstance(h, str) and h.isalnum() for h in hashes)\n",
    ),
    "a failed consistency is reported as differing checkpoints": (
        'return _Groups(stamped, later, small_text, small, large, "failed", ["CONSISTENCY_FAILED"])',
        'return _Groups(stamped, later, small_text, small, large, "failed", ["CHECKPOINTS_DIFFER"])',
    ),
    "later records are judged by the token window": ("        directory, groups.stamped, root,", "        directory, items, root,"),
    "later records are not listed": (
        'verdict["timing"]["later_items"] = [i.stem for i in groups.later]',
        'verdict["timing"]["later_items"] = []',
    ),
    # the settlement of a forecast in the later part
    "a settlement that a step cites and the bundle lacks is not refused": (
        '        if isinstance(cited, str):\n            shown["settlement"] = "absent"\n',
        '        if False:\n            shown["settlement"] = "absent"\n',
    ),
    "two settlements of one call are not refused": (
        '    if len(records) > 1:\n        shown["settlement"] = "conflicting"',
        '    if False:\n        shown["settlement"] = "conflicting"',
    ),
    "a settlement with another set of members is not refused": ("        or set(body) != _SETTLEMENT_MEMBERS\n", ""),
    "a state a call does not end in is not refused": ('        or body["state"] not in _SETTLEMENT_STATES\n', ""),
    "the types of the members are not read": (
        '        or not all(body[k] is None or isinstance(body[k], str) for k in ("outcome", "reason", "p0", "outcome_price"))\n',
        "",
    ),
    "the contract member is not read": ('        or not isinstance(body["contract"], str)\n', ""),
    "a reference to another record than the forecast is not refused": ("    wrong = not named\n", "    wrong = False\n"),
    "the id a step cites is not compared with the record's": (
        '        wrong = wrong or cited != record.get("id")\n',
        "        wrong = wrong\n",
    ),
    "the outcome is not held to the prices": (' or body["outcome"] != ("1" if last > first else "0")', ""),
    "a tie that is settled is accepted": ("wrong = wrong or last == first or body", "wrong = wrong or body"),
    "a void for a tie whose prices differ is accepted": ("            wrong = wrong or last != first\n", "            wrong = wrong\n"),
    "a missing first or last price is not refused": (
        "        if first is None or last is None:\n            wrong = True\n",
        "        if first is None or last is None:\n            wrong = wrong\n",
    ),
    "a price of zero is read as a price": ("    return number if number > 0 else None", "    return number"),
    "a contract other than the forecast's is accepted": (
        '    elif contract is not None and body["contract"] != contract:\n        wrong = True\n',
        "    elif False:\n        wrong = True\n",
    ),
    "the end of the call's window is not held": ("late = to_ms(item.committed_at) < origin_at + horizon", "late = False"),
    "a settlement that is faulted does not fail the call": (
        '"conflicting", "malformed") or faulted:',
        '"conflicting", "malformed"):',
    ),
    "a settlement written early is not a fault": (
        'SETTLEMENT_FAULTS: Final = ("absent", "conflicting", "malformed", "mismatch", "early")',
        'SETTLEMENT_FAULTS: Final = ("absent", "conflicting", "malformed", "mismatch")',
    ),
    "the later part is shown when the bundle is not intact": (
        'if not groups.later or groups.consistency != "verified" or not integrity_ok:',
        "if not groups.later:",
    ),
    "an eligible time before the derived one passes": ('awaiting = "ok" if eligible >= derived else "early"', 'awaiting = "ok"'),
    "an eligible time at the derived one fails": (
        'awaiting = "ok" if eligible >= derived else "early"',
        'awaiting = "ok" if eligible > derived else "early"',
    ),
    "an origin before the derived one passes": ("if origin_at < (derived // MINUTE_MS + 1) * MINUTE_MS:", "if False:"),
    "an origin that is not the next close passes": (
        'elif eligible is None or awaiting != "ok" or origin_at != (eligible // MINUTE_MS + 1) * MINUTE_MS:',
        "elif False:",
    ),
    "an origin after an eligible time that is not ok passes": (
        'elif eligible is None or awaiting != "ok" or origin_at',
        "elif eligible is None or origin_at",
    ),
    "a second waiting step is ignored": ('"conflicting" if len(waiting) > 1', '"ok" if len(waiting) > 1'),
    "a second opening step is ignored": ('"conflicting" if len(opened) > 1', '"ok" if len(opened) > 1'),
    "a missing waiting step passes": ('awaiting = "absent" if not waiting', 'awaiting = "ok" if not waiting'),
    "a malformed step time passes": ('"malformed" if eligible is None else "unchecked"', '"ok" if eligible is None else "unchecked"'),
    "origin failures do not make a bundle unscorable": (
        '    if verdict["cycle"]["later"]["failures"]:\n        reasons.append("ORIGIN_BEFORE_ATTESTATION")\n',
        "",
    ),
    "a committed level is taken from two steps": ("if len(committed) == 1 and isinstance", "if len(committed) >= 1 and isinstance"),
    "an ending is taken from two steps": ('"conflicting" if len(ended) > 1', 'ended[0]["state"] if len(ended) > 1'),
    "the stamped checkpoint is not the smaller one": (
        "known.sort(key=lambda entry: (entry[0], entry[1]))",
        "known.sort(key=lambda entry: (-entry[0], entry[1]))",
    ),
    # timing
    "the tokens stamp something other than the checkpoint": (
        'digest = hashlib.sha256(stamped_text).digest() if stamped_text is not None and items else b""',
        'digest = hashlib.sha256(b"").digest()',
    ),
    "no root is pinned for tokens": ("    policy = TokenPolicy(rules.pinned)", "    policy = TokenPolicy(())"),
    "unauthenticated bundles get a level": (
        '    if not authenticated or root is None:\n        out["reason"]',
        '    if False:\n        out["reason"]',
    ),
    "broken bundles get a level": ('    if not integrity_ok:\n        out["reason"]', '    if False:\n        out["reason"]'),
    "every record is a subject": (
        "    subjects = [i for i in items if i.window_opens_at is not None] or list(items)",
        "    subjects = list(items)",
    ),
    "one subject at C3 is enough": (
        "all(level == timing.LEVEL_C3 for level in levels)",
        "any(level == timing.LEVEL_C3 for level in levels)",
    ),
    "too many tokens are all read": ("        if index >= MAX_TOKENS:", "        if False:"),
    "token read errors are not reported": ("            evidence.append(timing.TokenEvidence(label, None, exc.code))", "            pass"),
    "public claims raise the level": (
        '"claimed": claims, "authenticated_offline": 0,',
        '"claimed": claims, "authenticated_offline": claims,',
    ),
    "unreadable publication files are ignored": (
        '            claims = len(data.get("claims", [])) if isinstance(data, dict) and isinstance(data.get("claims", []), list) else -1',
        "            claims = 0",
    ),
    # eligibility
    "an expired root can score": (
        '    if state != STATE_PINNED:\n        reasons.append("ROOT_NOT_CURRENT")',
        '    if False:\n        reasons.append("ROOT_NOT_CURRENT")',
    ),
    "broken integrity can score": (
        '    if not verdict["integrity"]["ok"]:\n        reasons.append("INTEGRITY")',
        '    if False:\n        reasons.append("INTEGRITY")',
    ),
    "a low level can score": ('    if verdict["timing"]["level"] != timing.LEVEL_C3:', "    if False:"),
    "any data mode can score": ("    if not modes or modes != {required}:", "    if False:"),
    "no evidence can score": ("    if not modes or modes != {required}:", "    if modes and modes != {required}:"),
    # the cycle's whole batch
    "an incomplete batch can score": (
        '    if not verdict["cycle"]["complete"]:\n        reasons.append("COMPLETENESS_NOT_SHOWN")',
        '    if False:\n        reasons.append("COMPLETENESS_NOT_SHOWN")',
    ),
    "the named root is not compared": (
        'out["complete"] = merkle.MerkleTree(leaves).root().hex() == named',
        'out["complete"] = True',
    ),
    "a missing member of the batch is tolerated": ('if out["members_present"] != count:', "if False:"),
    "a batch size of zero or below is read": ('or out["batch_size"] < 1\n', "or False\n"),
    "a boolean is read as a batch size": (
        'out["batch_size"] = size if isinstance(size, int) and not isinstance(size, bool) else None',
        'out["batch_size"] = size if isinstance(size, int) else None',
    ),
    "a committing step that failed its proof is trusted": (
        "        step.error is not None\n        or not step.canonical_ok\n        or not step.inclusion\n",
        "        False\n",
    ),
    "members that failed their proof are counted": (
        "if i.proof is not None and i.error is None and i.canonical_ok and i.inclusion}",
        "if i.proof is not None}",
    ),
    "more than one committing step is accepted": ("if len(steps) != 1 or not checkpoints_ok:", "if not steps or not checkpoints_ok:"),
    "a bundle with no forecast can score": (
        '    if forecasts == 0:\n        reasons.append("NO_FORECAST")',
        '    if False:\n        reasons.append("NO_FORECAST")',
    ),
    "any record counts as a forecast": (
        'i.record is not None and i.record.get("type") == FORECAST_TYPE and i.error is None and i.inclusion)',
        "i.record is not None and i.error is None and i.inclusion)",
    ),
    "a forecast that failed its proof is counted": (
        'i.record.get("type") == FORECAST_TYPE and i.error is None and i.inclusion)',
        'i.record.get("type") == FORECAST_TYPE)',
    ),
    "a missing data mode is ignored": (
        'modes.add(mode if isinstance(mode, str) else "missing")',
        "modes.add(mode) if isinstance(mode, str) else None",
    ),
}


@pytest.fixture(scope="module")
def cases(tmp_path_factory: pytest.TempPathFactory) -> list[Case]:
    directory = tmp_path_factory.mktemp("bundles")
    return build_cases(directory) + build_special_cases(directory)


def test_real_verifier_meets_every_expectation(cases: list[Case]) -> None:
    assert failures(verify, cases) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str, cases: list[Case]) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.verify", old, new), cases), f"mutant not detected: {name}"


def test_verdict_does_not_depend_on_where_the_bundle_is(cases: list[Case], tmp_path: Path) -> None:
    import json
    import shutil

    honest = next(c for c in cases if c.name == "honest")
    copy = tmp_path / "elsewhere"
    shutil.copytree(honest.directory, copy)
    first = verify.verify_bundle(honest.directory, honest.root)
    second = verify.verify_bundle(copy, honest.root)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
