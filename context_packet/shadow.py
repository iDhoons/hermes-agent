"""Phase 1 - shadow comparison (read-only, live output unchanged).

A shadow comparison pairs a domain's Context Packet with the live "view" that
some other tool currently produces (for ``daily-focus`` that is the
``tmux-today-briefing`` output), and reports whether the packet's required
evidence is actually confirmed. It DIFFS, it does not inject: the live view file
is only read, never written, and no prompt/cron/service is touched.

Divergence signal (generic across domains):
  * confirmed_evidence   = probes that resolved live (status "ok")
  * unconfirmed_evidence = probes still skipped/failed (e.g. PIM/calendar plans,
                           a missing daily note)
  * divergence = there is unconfirmed evidence while a live view is presenting
    content - i.e. the live tool may be asserting state the packet would flag.

A later phase could run this on a timer and alert on divergence; that is a
scheduler change and is intentionally NOT wired here (separate approval gate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .builder import build_packet
from .probes import ProbeRunner

SHADOW_SCHEMA_VERSION = "context-packet-shadow-v0"


def build_shadow_comparison(
    domain: str,
    *,
    registry_path=None,
    vault_root: str | None = None,
    home_root: str | None = None,
    now: datetime | None = None,
    probe_runner: ProbeRunner | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    packet = build_packet(
        domain, registry_path=registry_path, vault_root=vault_root,
        home_root=home_root, now=now, probe_runner=probe_runner)

    # The live view = the first allowed view (read-only).
    allowed = packet.views.get("allowed", [])
    live_view: dict | None = None
    if allowed:
        p = Path(allowed[0]["path"])
        if p.exists():
            lines = [ln.strip() for ln in
                     p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.strip()]
            live_view = {
                "name": allowed[0].get("name"), "path": str(p), "exists": True,
                "observed_at": now.isoformat(), "line_count": len(lines),
                "content_summary": " / ".join(lines[:5])[:400],
            }
        else:
            live_view = {
                "name": allowed[0].get("name"), "path": str(p), "exists": False,
                "observed_at": now.isoformat(), "line_count": 0,
                "content_summary": "",
            }

    confirmed = [p.name for p in packet.live_probes if p.status == "ok"]
    unconfirmed = [
        {"name": p.name, "status": p.status, "why": p.result_summary}
        for p in packet.live_probes if p.status in ("skipped", "failed")
    ]
    live_present = bool(live_view and live_view["exists"]
                        and live_view["line_count"] > 0)
    view_name = live_view["name"] if live_view else "view"
    divergence = bool(unconfirmed)

    if divergence and live_present:
        note = (
            f"Live '{view_name}' view presents content, but "
            f"{len(unconfirmed)} required evidence probe(s) are unconfirmed: "
            f"{', '.join(u['name'] for u in unconfirmed)}. A packet-guided answer "
            "would flag these as unverified rather than assert state.")
    elif divergence:
        note = (f"{len(unconfirmed)} evidence probe(s) unconfirmed; no live view "
                "content to compare against.")
    else:
        note = "All probes confirmed; live view and packet evidence agree."

    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "domain": domain,
        "mode": "shadow",          # never injected into any prompt/service/cron
        "generated_at": now.isoformat(),
        "live_view": live_view,
        "comparison": {
            "live_view_present": live_present,
            "confirmed_evidence": confirmed,
            "unconfirmed_evidence": unconfirmed,
            "divergence": divergence,
            "packet_warnings": packet.warnings,
            "note": note,
        },
        "packet": packet.to_dict(),
    }


def render_shadow_markdown(comp: dict) -> str:
    c = comp["comparison"]
    lv = comp.get("live_view")
    lines = [
        f"## Shadow comparison - {comp['domain']} (mode: {comp['mode']})",
        f"_generated_at {comp['generated_at']} - "
        f"divergence: {'YES' if c['divergence'] else 'no'}_",
        "",
        "### Live view (read-only, not modified)",
    ]
    if lv and lv["exists"]:
        lines.append(f"- {lv['name']}: `{lv['path']}` ({lv['line_count']} lines)")
        lines.append(f"  > {lv['content_summary']}")
    elif lv:
        lines.append(f"- {lv['name']}: `{lv['path']}` (MISSING)")
    else:
        lines.append("- (no allowed view configured)")
    lines.append("")

    lines.append("### Evidence")
    lines.append(f"- confirmed (live ok): {', '.join(c['confirmed_evidence']) or 'none'}")
    if c["unconfirmed_evidence"]:
        lines.append("- unconfirmed:")
        for u in c["unconfirmed_evidence"]:
            lines.append(f"  - [{u['status']}] {u['name']}: {u['why']}")
    lines.append("")
    if c["packet_warnings"]:
        lines.append("### Packet warnings")
        lines.extend(f"- {w}" for w in c["packet_warnings"])
        lines.append("")
    lines.append(f"### Verdict\n{c['note']}")
    return "\n".join(lines).rstrip() + "\n"
