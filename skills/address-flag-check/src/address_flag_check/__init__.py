"""address_flag_check: a read-only research skill that reports whether an address is on OFAC's SDN list or frozen by a stablecoin issuer."""

__version__ = "0.1.0"

from .errors import InputError, SourceError
from .schema import OUTPUT_SCHEMA, SOURCES_OUTPUT_SCHEMA, TOOL_DEFINITION, TOOL_DEFINITIONS, TOOL_NAME
from .skill import run, run_sources

__all__ = [
    "OUTPUT_SCHEMA",
    "SOURCES_OUTPUT_SCHEMA",
    "TOOL_DEFINITION",
    "TOOL_DEFINITIONS",
    "TOOL_NAME",
    "InputError",
    "SourceError",
    "__version__",
    "run",
    "run_sources",
]
