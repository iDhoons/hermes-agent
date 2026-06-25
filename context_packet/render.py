"""Render a :class:`ContextPacket` as JSON or a compact markdown block.

The markdown form is a single block suitable for a later phase to prepend to a
prompt (Phase 3). It is intentionally compact and always shows *when* state was
observed and which sources are NOT authority.
"""

from __future__ import annotations

import json

from .models import ContextPacket

_STATUS_ICON = {"ok": "[ok]", "failed": "[FAIL]", "skipped": "[plan]"}


def render_json(packet: ContextPacket) -> str:
    return json.dumps(packet.to_dict(), indent=2, ensure_ascii=False)


def render_markdown(packet: ContextPacket) -> str:
    a = packet.authority
    lines: list[str] = []
    lines.append(f"## Context Packet - {packet.domain}")
    lines.append(
        f"_generated_at {packet.generated_at} - confidence "
        f"{a.get('confidence')} - owner {a.get('owner')}_")
    lines.append("")

    lines.append("### Authority (intent - by pointer)")
    for src in a.get("intent_sources", []):
        if src.get("exists"):
            meta = f"updated {src.get('updated')}, {src.get('staleness')}"
        else:
            meta = "MISSING"
        lines.append(f"- `{src['path']}` - {src['owns']} ({meta})")
    lines.append("")

    lines.append("### Live state (probe = truth)")
    for p in packet.live_probes:
        icon = _STATUS_ICON.get(p.status, p.status)
        lines.append(f"- {icon} **{p.name}**: {p.result_summary}")
    lines.append("")

    views = packet.views
    if views.get("allowed"):
        lines.append("### Views (derived - not authority)")
        for v in views["allowed"]:
            lines.append(f"- {v.get('name')}: `{v.get('path')}`")
    if views.get("not_authority"):
        lines.append("**NOT authority:**")
        for n in views["not_authority"]:
            lines.append(f"- {n}")
    lines.append("")

    hp = packet.history_policy
    lines.append(
        f"### History policy: {hp.get('default')} "
        f"(allowed when: {', '.join(hp.get('allowed_when', [])) or 'never'})")
    lines.append("")

    if packet.defaults_and_guards:
        lines.append("### Defaults & guards")
        lines.extend(f"- {g}" for g in packet.defaults_and_guards)
        lines.append("")

    if packet.do_not:
        lines.append("### Do NOT")
        lines.extend(f"- {d}" for d in packet.do_not)
        lines.append("")

    if packet.warnings:
        lines.append("### Warnings")
        lines.extend(f"- {w}" for w in packet.warnings)
        lines.append("")

    if packet.recommended_use:
        lines.append(f"### Recommended use\n{packet.recommended_use}")

    return "\n".join(lines).rstrip() + "\n"
