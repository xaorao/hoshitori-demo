"""Natural-key identifiers: ``prefix`` plus 26 base32 characters of the SHA-256 of the key's canonical form.

The same key always gives the same id, so replaying a record never makes a second thing; two different keys do not share an id
(``DOMAIN_MODEL`` section 3). The key is a record in the canonical profile (strings, integers, booleans, ``null``, arrays and objects).
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Final

from hoshitori import canonical

PREFIX: Final = re.compile(r"[a-z]{2,4}")


def natural_id(prefix: str, key: dict[str, canonical.JsonValue]) -> str:
    if PREFIX.fullmatch(prefix) is None:
        raise ValueError("an id prefix is two to four lower-case letters")
    digest = hashlib.sha256(canonical.dumps(key)).digest()
    return prefix + base64.b32encode(digest).decode("ascii").lower()[:26]  # the padding of 32 bytes starts after the 52nd character
