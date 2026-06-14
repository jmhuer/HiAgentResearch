"""Canonical path-prefix predicates shared across config, orchestrator, and git.

These encode the one rule the framework cares about: is a repo-relative path equal
to, or nested under, a given root/prefix. Trailing slashes are insignificant and an
empty/`.` root means "the whole tree".
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch


def is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` itself or nested under it."""
    normalized = path.rstrip("/")
    root_normalized = root.rstrip("/")
    if root_normalized in ("", "."):
        return True
    return normalized == root_normalized or normalized.startswith(f"{root_normalized}/")


def is_under_any(path: str, prefixes: Iterable[str]) -> bool:
    """True when ``path`` is within any of the (non-empty) ``prefixes``."""
    normalized = path.rstrip("/")
    for prefix in prefixes:
        prefix_normalized = prefix.rstrip("/")
        if not prefix_normalized:
            continue
        if normalized == prefix_normalized or normalized.startswith(f"{prefix_normalized}/"):
            return True
    return False


def matches_any(path: str, specs: Iterable[str]) -> bool:
    """True when ``path`` matches any exact/prefix spec or shell-style glob."""
    normalized = path.rstrip("/")
    for spec in specs:
        spec_normalized = spec.rstrip("/")
        if not spec_normalized:
            continue
        if any(char in spec_normalized for char in "*?[]"):
            if fnmatch(normalized, spec_normalized):
                return True
            continue
        if is_within(normalized, spec_normalized):
            return True
    return False
