"""``python -m context_packet`` - read-only Context Packet generator (Phase 0).

Examples::

    python -m context_packet --list
    python -m context_packet paperclip.issue
    python -m context_packet infra.hermes --format json
    python -m context_packet daily-focus --format both --out-dir /tmp/packets
    python -m context_packet daily-focus --shadow   # Phase 1: diff vs live view

By default it writes nothing - it prints to stdout. ``--out-dir`` is an opt-in
shadow artifact sink (a later phase's diff target); it never injects into any
prompt, service, cron, or DB.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .builder import build_packet
from .registry import list_domains, load_registry
from .render import render_json, render_markdown
from .shadow import build_shadow_comparison, render_shadow_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="context-packet",
        description="Generate a read-only Context Packet for a task domain.",
    )
    p.add_argument("domain", nargs="?", help="domain, e.g. paperclip.issue")
    p.add_argument("--list", action="store_true", help="list known domains")
    p.add_argument(
        "--shadow", action="store_true",
        help="Phase 1: emit a shadow comparison vs the live view (read-only)")
    p.add_argument("--format", choices=["md", "json", "both"], default="md")
    p.add_argument("--registry", help="override registry path")
    p.add_argument("--vault-root", help="override roots.vault")
    p.add_argument("--home-root", help="override roots.home")
    p.add_argument(
        "--out-dir",
        help="opt-in: also write <domain>.json/.md shadow artifacts here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:  # bad --registry is the same input-error class as a bad domain -> exit 2
        registry = load_registry(args.registry)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot load registry {args.registry or '(default)'}: {exc}",
              file=sys.stderr)
        return 2

    if args.list:
        for d in list_domains(registry):
            print(d)
        return 0

    if not args.domain:
        print("error: a domain is required (or use --list)", file=sys.stderr)
        return 2

    if args.domain not in registry.get("domains", {}):
        known = ", ".join(list_domains(registry))
        print(f"error: unknown domain {args.domain!r}. known: {known}",
              file=sys.stderr)
        return 2

    if args.shadow:
        comp = build_shadow_comparison(
            args.domain, registry_path=args.registry,
            vault_root=args.vault_root, home_root=args.home_root)
        md = render_shadow_markdown(comp)
        js = json.dumps(comp, indent=2, ensure_ascii=False)
        suffix = "shadow"
    else:
        packet = build_packet(
            args.domain,
            registry_path=args.registry,
            vault_root=args.vault_root,
            home_root=args.home_root,
        )
        md = render_markdown(packet)
        js = render_json(packet)
        suffix = "packet"

    if args.format == "md":
        print(md)
    elif args.format == "json":
        print(js)
    else:
        print(md)
        print(js)

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # sanitize the domain into a flat filename so a custom registry key
        # cannot traverse out of --out-dir (e.g. "../../etc/x").
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", args.domain) + f".{suffix}"
        (out / f"{safe}.json").write_text(js, encoding="utf-8")
        (out / f"{safe}.md").write_text(md, encoding="utf-8")
        print(f"# wrote artifacts to {out}/{safe}.{{json,md}}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
