"""Exceptions raised on purpose by ratersheet.

Every deliberate failure is a ``RatersheetError`` so that callers (and the
command line) can tell "the tool refused, and here is why" apart from a bug.
"""

from __future__ import annotations


class RatersheetError(Exception):
    """Base class for everything the tool raises on purpose."""


class ConfigError(RatersheetError):
    """The study file or the items file is not usable."""


class IntegrityError(RatersheetError):
    """The key, the manifest or a commitment do not match each other."""
