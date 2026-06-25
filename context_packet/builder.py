"""Assemble a read-only Context Packet for a domain.

Build steps:

  1. resolve the domain entry from the thin registry (paths expanded),
  2. turn each intent_authority pointer into an :class:`IntentSource`
     (existence + staleness computed from file mtime - never its contents),
  3. run each state_probe through the read-only :class:`ProbeRunner`,
  4. derive warnings from observed results (failed probes, stale intent),
  5. stamp ``generated_at`` from the injected clock and return the packet.

The builder writes nothing. Output is produced by the renderers / CLI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ContextPacket, IntentSource, ProbeResult
from .probes import ProbeRunner, _classify_staleness
from .registry import load_registry, resolve_domain, resolve_roots

PACKET_SCHEMA_VERSION = "context-packet-v0"


class UnknownDomain(KeyError):
    """Requested domain is not in the registry."""


def _intent_source(item: dict[str, Any], now: datetime) -> IntentSource:
    path = Path(item["path"])
    if not path.exists():
        return IntentSource(
            path=str(path), owns=item.get("owns", ""), exists=False,
            staleness="unknown",
        )
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    days = max(0, (now - mtime).days)
    return IntentSource(
        path=str(path), owns=item.get("owns", ""), exists=True,
        updated=mtime.strftime("%Y-%m-%d"),
        staleness=_classify_staleness(days), staleness_days=days,
    )


def _derive_warnings(
    intents: list[IntentSource], probes: list[ProbeResult],
) -> list[str]:
    warnings: list[str] = []
    for src in intents:
        if not src.exists:
            warnings.append(f"intent authority missing: {src.path}")
        elif src.staleness == "stale":
            warnings.append(
                f"intent authority stale ({src.staleness_days}d): {src.path}")
    for p in probes:
        if p.status == "failed":
            warnings.append(f"probe failed: {p.name} - {p.result_summary}")
    return warnings


def build_packet(
    domain: str,
    *,
    registry_path: str | Path | None = None,
    vault_root: str | None = None,
    home_root: str | None = None,
    now: datetime | None = None,
    probe_runner: ProbeRunner | None = None,
) -> ContextPacket:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:  # tolerate a naive injected clock (treat as UTC)
        now = now.replace(tzinfo=timezone.utc)
    registry = load_registry(registry_path)
    if domain not in registry.get("domains", {}):
        raise UnknownDomain(domain)

    roots = resolve_roots(registry, vault_root=vault_root, home_root=home_root)
    entry = resolve_domain(registry, domain, roots, now)
    runner = probe_runner or ProbeRunner(now=now)

    intents = [_intent_source(i, now) for i in entry.get("intent_authority", [])]
    probes = [runner.run(spec) for spec in entry.get("state_probes", [])]

    state_sources = [
        {"name": s.get("name"), "kind": s.get("kind"),
         "ref": s.get("path") or s.get("url") or s.get("command")}
        for s in entry.get("state_probes", [])
    ]

    authority = {
        "intent_sources": [vars(i) for i in intents],
        "state_sources": state_sources,
        "owner": entry.get("owner"),
        "confidence": entry.get("confidence", "low"),
    }

    warnings = _derive_warnings(intents, probes)

    return ContextPacket(
        schema_version=PACKET_SCHEMA_VERSION,
        domain=domain,
        generated_at=now.isoformat(),
        authority=authority,
        live_probes=probes,
        views=entry.get("views", {"allowed": [], "not_authority": []}),
        history_policy=entry.get(
            "history_policy", {"default": "exclude", "allowed_when": []}),
        defaults_and_guards=entry.get("defaults_and_guards", []),
        do_not=entry.get("do_not", []),
        warnings=warnings,
        recommended_use=entry.get("recommended_use", ""),
    )
