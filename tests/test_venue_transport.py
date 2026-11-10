"""The venue transport against a fake server on loopback: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_transport
from tests.mutation import mutant
from tests.venue_transport_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(venue_transport) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "the adapter does not build the request": (
        "            request = self._adapter.build_request(operation, args)",
        "            request = VenueRequest('GET', '/time')",
    ),
    "the prefix is dropped": ("self._prefix + request.path", "request.path"),
    "the method is always GET": ("operation, args, request.method, self._prefix", "operation, args, 'GET', self._prefix"),
    "the body is dropped": ("self._prefix + request.path, request.body, {}", "self._prefix + request.path, None, {}"),
    "a credential is sent": (
        "self._prefix + request.path, request.body, {}",
        "self._prefix + request.path, request.body, {'Authorization': 'Bearer x'}",
    ),
    "the operation is recorded wrongly": ("operation, args, request.method", "'time', args, request.method"),
    "the arguments are recorded wrongly": ("operation, args, request.method", "operation, {}, request.method"),
    "the provider is the venue": ("            provider=adapter.provider,", "            provider=adapter.venue,"),
    "the provider is fixed": ("            provider=adapter.provider,", "            provider='venue',"),
    "a connector error is not caught": (
        "        except (ConnectorError, KeyError, ValueError, TypeError) as exc:",
        "        except (KeyError, ValueError, TypeError) as exc:",
    ),
    "a key error is not caught": (
        "        except (ConnectorError, KeyError, ValueError, TypeError) as exc:",
        "        except (ConnectorError, ValueError, TypeError) as exc:",
    ),
    "a value error is not caught": (
        "        except (ConnectorError, KeyError, ValueError, TypeError) as exc:",
        "        except (ConnectorError, KeyError, TypeError) as exc:",
    ),
    "a type error is not caught": (
        "        except (ConnectorError, KeyError, ValueError, TypeError) as exc:",
        "        except (ConnectorError, KeyError, ValueError) as exc:",
    ),
    "every error is caught": (
        "        except (ConnectorError, KeyError, ValueError, TypeError) as exc:",
        "        except Exception as exc:",
    ),
    "a build failure is upstream": (
        'raise TransportFailure(ErrorClass.INVALID_ARGS, f"the request could not be built: {type(exc).__name__}") from None',
        'raise TransportFailure(ErrorClass.UPSTREAM, f"the request could not be built: {type(exc).__name__}") from None',
    ),
    "a build failure says what went wrong": (
        'f"the request could not be built: {type(exc).__name__}"',
        'f"the request could not be built: {exc}"',
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_transport", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
