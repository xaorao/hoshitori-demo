"""ryo_verdict_record: a read-only research skill that reports how RYO's own verdicts settled, from a published Hoshitori record."""

__version__ = "0.1.0"

from .errors import InputError
from .schema import OUTPUT_SCHEMA, RULES_OUTPUT_SCHEMA, TOOL_DEFINITION, TOOL_DEFINITIONS, TOOL_NAME
from .skill import run, run_rules

__all__ = [
    "OUTPUT_SCHEMA",
    "RULES_OUTPUT_SCHEMA",
    "TOOL_DEFINITION",
    "TOOL_DEFINITIONS",
    "TOOL_NAME",
    "InputError",
    "__version__",
    "run",
    "run_rules",
]
