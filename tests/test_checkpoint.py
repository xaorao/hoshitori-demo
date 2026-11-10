"""Checkpoints: format, canonical form, origin binding and signature rules."""

from __future__ import annotations

import base64

import pytest

from hoshitori import checkpoint as K
from hoshitori import note as N

SIGNER = N.Signer.from_seed("example.org/hoshitori-test-log", bytes(range(32)))
ROOT = bytes(range(32))


def signed(size: int = 20852163, root: bytes = ROOT, origin: str = "example.org/hoshitori-test-log") -> bytes:
    return K.sign_checkpoint(K.Checkpoint(origin, size, root), [SIGNER])


def test_text_format_matches_the_specification_example_shape() -> None:
    root = base64.b64decode("CsUYapGGPo4dkMgIAUqom/Xajj7h2fB2MPA3j2jxq2I=")
    text = K.Checkpoint("example.com/behind-the-sofa", 20852163, root).text()
    assert text == b"example.com/behind-the-sofa\n20852163\nCsUYapGGPo4dkMgIAUqom/Xajj7h2fB2MPA3j2jxq2I=\n"
    assert K.parse_text(text) == K.Checkpoint("example.com/behind-the-sofa", 20852163, root)


def test_round_trip() -> None:
    cp = K.verify_checkpoint(signed(), [SIGNER.verifier], "example.org/hoshitori-test-log")
    assert cp == K.Checkpoint("example.org/hoshitori-test-log", 20852163, ROOT)
    assert K.verify_checkpoint(signed(0), [SIGNER.verifier], "example.org/hoshitori-test-log").size == 0


def test_another_logs_checkpoint_is_refused() -> None:
    with pytest.raises(K.CheckpointError, match="another log"):
        K.verify_checkpoint(signed(), [SIGNER.verifier], "example.org/other-log")


def test_a_checkpoint_signed_by_an_untrusted_key_is_refused() -> None:
    other = N.Signer.from_seed("example.org/hoshitori-test-log", bytes(range(1, 33)))
    with pytest.raises(K.CheckpointError):
        K.verify_checkpoint(
            K.sign_checkpoint(K.Checkpoint("example.org/hoshitori-test-log", 5, ROOT), [other]),
            [SIGNER.verifier],
            "example.org/hoshitori-test-log",
        )


@pytest.mark.parametrize(
    "text",
    [
        b"origin\n01\n" + base64.b64encode(ROOT) + b"\n",  # leading zero
        b"origin\n+5\n" + base64.b64encode(ROOT) + b"\n",  # sign
        b"origin\n5 \n" + base64.b64encode(ROOT) + b"\n",  # trailing space
        b"origin\n\xd9\xa5\n" + base64.b64encode(ROOT) + b"\n",  # non-ASCII digit
        b"origin\n-1\n" + base64.b64encode(ROOT) + b"\n",
        b"origin\n5\n" + base64.b64encode(ROOT[:31]) + b"\n",  # short root
        b"origin\n5\n" + base64.b64encode(ROOT + b"\x00") + b"\n",  # long root
        b"origin\n5\n" + base64.b64encode(ROOT).rstrip(b"=") + b"\n",  # padding removed
        b"origin\n5\n" + base64.b64encode(ROOT) + b"\nextension\n",  # extension line
        b"origin\n5\n\n",  # empty root line
        b"\n5\n" + base64.b64encode(ROOT) + b"\n",  # empty origin
        b"origin\n5\n" + base64.b64encode(ROOT),  # no final newline
        b"origin 5\n5\n" + base64.b64encode(ROOT) + b"\n",  # space in origin
        b"origin\n9223372036854775808\n" + base64.b64encode(ROOT) + b"\n",  # beyond 2**63 - 1
    ],
)
def test_malformed_checkpoint_text_is_rejected(text: bytes) -> None:
    with pytest.raises(K.CheckpointError):
        K.parse_text(text)


def test_noncanonical_but_parsable_text_cannot_be_signed_into_a_verified_checkpoint() -> None:
    # extension lines are signed by the note layer but a verifier refuses them
    text = b"example.org/hoshitori-test-log\n5\n" + base64.b64encode(ROOT) + b"\nsomething\n"
    with pytest.raises(K.CheckpointError):
        K.verify_checkpoint(N.sign_note(text, [SIGNER]), [SIGNER.verifier], "example.org/hoshitori-test-log")


def test_constructor_validates() -> None:
    for args in (("", 1, ROOT), ("o", -1, ROOT), ("o", 2**63, ROOT), ("o", 1, ROOT[:5]), ("o x", 1, ROOT), ("o", True, ROOT)):
        with pytest.raises(K.CheckpointError):
            K.Checkpoint(*args)  # type: ignore[arg-type]
