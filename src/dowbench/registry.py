"""Load pluggable components from entry points (ADR 0021).

Providers, defenses and attack packs are discovered through
``importlib.metadata.entry_points``, so a third party can add one from its own installed
package without editing dowbench. The built-ins are registered the same way in
``pyproject.toml`` under the groups ``dowbench.providers``, ``dowbench.defenses`` and
``dowbench.attacks``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Any

# A source of entry points for a group; injectable so tests can register a fake plugin
# without installing a package.
EntryPointSource = Callable[[str], Iterable[EntryPoint]]


def _installed(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


def names(group: str, *, source: EntryPointSource = _installed) -> list[str]:
    """The registered names in ``group``, sorted."""
    return sorted(ep.name for ep in source(group))


def load(group: str, name: str, *, source: EntryPointSource = _installed) -> Any:
    """Load the object registered as ``name`` in ``group``.

    Raises ``LookupError`` naming the registered alternatives when ``name`` is unknown, so
    a typo in a config points the user at what is available.
    """
    for ep in source(group):
        if ep.name == name:
            return ep.load()
    known = ", ".join(names(group, source=source)) or "(none)"
    raise LookupError(f"unknown {group} entry {name!r}; registered: {known}")


def load_all(group: str, *, source: EntryPointSource = _installed) -> dict[str, Any]:
    """Load every object registered in ``group``, keyed by name."""
    return {ep.name: ep.load() for ep in source(group)}
