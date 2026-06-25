"""Read-only Context Packet / Authority Router - Phase 0.

A thin authority registry (routing) plus a read-only packet builder (live probe
= truth). Self-contained: imports only the standard library, wires into no
Hermes runtime, and writes nothing unless explicitly asked (``--out-dir``).

Promotion path (a later phase): add ``hermes_cli/subcommands/context_packet.py``
that calls :func:`context_packet.cli.main` and register it in ``main.py``.
"""

from __future__ import annotations

from .builder import UnknownDomain, build_packet
from .models import ContextPacket, IntentSource, ProbeResult
from .render import render_json, render_markdown

__all__ = [
    "ContextPacket",
    "IntentSource",
    "ProbeResult",
    "UnknownDomain",
    "build_packet",
    "render_json",
    "render_markdown",
]
