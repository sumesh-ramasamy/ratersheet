"""ratersheet: blinded, balanced rating sheets for human evaluation of AI-generated text."""

# The version is defined before the imports below because the submodules read
# it from here (the build backend also reads it from this file).
__version__ = "0.1.0"

from .assign import Assignment, Design, design_summary, make_design  # noqa: E402
from .build import BuildResult, build  # noqa: E402
from .collect import CollectResult, Problem, collect  # noqa: E402
from .config import Item, Scale, Study, load_study  # noqa: E402
from .errors import ConfigError, IntegrityError, RatersheetError  # noqa: E402
from .integrity import file_sha256, normalize_text, text_sha256  # noqa: E402
from .leaks import LeakCoverage, LeakWarning, check_leaks, leak_coverage  # noqa: E402
from .methods import methods_text, write_methods  # noqa: E402
from .rng import DeterministicRNG  # noqa: E402
from .unblind import UnblindResult, unblind, verify_key, verify_sheet  # noqa: E402
from .workbook import (  # noqa: E402
    ReturnedRow,
    ReturnedWorkbook,
    SheetRow,
    read_workbook,
    write_workbook,
)

__all__ = [
    "Assignment",
    "BuildResult",
    "CollectResult",
    "ConfigError",
    "Design",
    "DeterministicRNG",
    "IntegrityError",
    "Item",
    "LeakCoverage",
    "LeakWarning",
    "Problem",
    "RatersheetError",
    "ReturnedRow",
    "ReturnedWorkbook",
    "Scale",
    "SheetRow",
    "Study",
    "UnblindResult",
    "__version__",
    "build",
    "check_leaks",
    "collect",
    "design_summary",
    "file_sha256",
    "leak_coverage",
    "load_study",
    "make_design",
    "methods_text",
    "normalize_text",
    "read_workbook",
    "text_sha256",
    "unblind",
    "verify_key",
    "verify_sheet",
    "write_methods",
    "write_workbook",
]
