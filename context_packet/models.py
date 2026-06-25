"""Typed structures for Context Packets.

A Context Packet is a read-only, as-of snapshot that separates four kinds of
sources for a task domain:

  * Authority (intent truth)  - what we are trying to do, by pointer.
  * Live probes (state truth) - what is actually true right now, fetched live.
  * Views (derived snapshots)  - compiled current-state views, NOT authority.
  * History (append-only)      - what happened; excluded by default.

Nothing here copies current truth. Pointers + probe results carry timestamps so
a reader can always tell *when* something was observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class IntentSource:
    """A pointer to an authority document (never its copied contents)."""

    path: str
    owns: str
    exists: bool
    updated: str | None = None          # YYYY-MM-DD (file mtime)
    staleness: str | None = None        # fresh | aging | stale | unknown
    staleness_days: int | None = None


@dataclass
class ProbeResult:
    """Outcome of one live (or planned) read-only probe."""

    name: str
    status: str                         # ok | failed | skipped
    result_summary: str
    evidence: str
    observed_at: str | None = None


@dataclass
class ContextPacket:
    """The full packet. Field set is stable across every domain."""

    schema_version: str
    domain: str
    generated_at: str
    authority: dict[str, Any]
    live_probes: list[ProbeResult]
    views: dict[str, Any]
    history_policy: dict[str, Any]
    defaults_and_guards: list[str] = field(default_factory=list)
    do_not: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_use: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
