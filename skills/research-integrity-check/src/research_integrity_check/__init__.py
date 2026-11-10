"""research_integrity_check: a read-only research skill that says what a market claim is made of and what nothing has checked yet."""

__version__ = "0.1.0"

from .errors import InputError
from .schema import OUTPUT_SCHEMA, TOOL_DEFINITION, TOOL_DEFINITIONS, TOOL_NAME
from .skill import run

__all__ = ["OUTPUT_SCHEMA", "TOOL_DEFINITION", "TOOL_DEFINITIONS", "TOOL_NAME", "InputError", "__version__", "run"]
