"""Load and resolve the thin authority registry.

The registry holds pointers + probe specs + policy text only - never copied
truth. This module loads it and expands path placeholders:

  ``{vault}``       -> roots.vault
  ``{home}``        -> roots.home
  ``{year_month}``  -> YYYY-MM   (from the build clock, local time)
  ``{date}``        -> YYYY-MM-DD (from the build clock, local time)
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY = Path(__file__).with_name("registry.json")


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    src = Path(path) if path else DEFAULT_REGISTRY
    return json.loads(src.read_text(encoding="utf-8"))


def resolve_roots(
    registry: dict[str, Any],
    *,
    vault_root: str | None = None,
    home_root: str | None = None,
) -> dict[str, str]:
    roots = dict(registry.get("roots", {}))
    if vault_root:
        roots["vault"] = vault_root
    if home_root:
        roots["home"] = home_root
    return roots


def expand_path(template: str, roots: dict[str, str], now: datetime) -> str:
    local = now.astimezone()
    return (
        template
        .replace("{vault}", roots.get("vault", ""))
        .replace("{home}", roots.get("home", ""))
        .replace("{year_month}", local.strftime("%Y-%m"))
        .replace("{date}", local.strftime("%Y-%m-%d"))
    )


def resolve_domain(
    registry: dict[str, Any],
    domain: str,
    roots: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    """Return a deep copy of one domain entry with every ``path`` expanded."""
    entry = copy.deepcopy(registry["domains"][domain])

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: (expand_path(v, roots, now)
                    if k == "path" and isinstance(v, str) else _walk(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        return obj

    return _walk(entry)


def list_domains(registry: dict[str, Any]) -> list[str]:
    return sorted(registry.get("domains", {}).keys())
