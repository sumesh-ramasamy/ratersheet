"""The public names of the package: what `from ratersheet import ...` promises."""

from __future__ import annotations

import inspect

import ratersheet
from ratersheet import leaks


def test_every_exported_name_exists() -> None:
    for name in ratersheet.__all__:
        assert hasattr(ratersheet, name), f"__all__ lists {name!r}, which the package does not have"
    assert list(ratersheet.__all__) == sorted(set(ratersheet.__all__)), "sorted, nothing twice"


def test_leak_coverage_is_exported() -> None:
    # "No warnings" can only be read correctly beside what the checks could look at.
    assert "LeakCoverage" in ratersheet.__all__ and "leak_coverage" in ratersheet.__all__
    assert ratersheet.LeakCoverage is leaks.LeakCoverage
    assert ratersheet.leak_coverage is leaks.leak_coverage


def test_make_design_takes_the_secret_by_keyword_only() -> None:
    parameters = inspect.signature(ratersheet.make_design).parameters
    assert list(parameters) == ["study", "secret"]
    assert parameters["secret"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["secret"].default is inspect.Parameter.empty, "there is no default secret"
