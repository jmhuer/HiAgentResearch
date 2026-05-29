"""Canonical path-prefix predicates shared across config, orchestrator, and git.

These encode the one rule the framework cares about: is a repo-relative path equal
to, or nested under, a given root/prefix. Trailing slashes are insignificant and an
empty/`.` root means "the whole tree".
"""

from __future__ import annotations

from collections.abc import Iterable


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
