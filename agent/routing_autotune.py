"""Offline helpers for improving smart model routing from telemetry.

This module is intentionally side-effect free: it reads redacted routing decision
records and returns conservative suggestions. It does not modify config files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


def load_routing_decisions(path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load JSONL routing decision records, skipping malformed rows."""
    decision_path = Path(path).expanduser()
    if not decision_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with decision_path.open("r", encoding="utf-8") as f:
        for line in f:
            if limit is not None and len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
    return records


def suggest_routing_improvements(
    records: Iterable[dict[str, Any]],
    routing_config: dict[str, Any],
    *,
    min_samples: int = 20,
) -> list[dict[str, Any]]:
    """Suggest conservative config changes from redacted routing telemetry.

    Current heuristic: if many safe, non-complex, non-protected requests fell
    through to primary, suggest raising the simple lane thresholds to their p90.
    This is intentionally recommendation-only; callers decide whether to apply.
    """
    safe_primary = [row for row in records if _is_safe_primary_spillover(row)]
    if len(safe_primary) < min_samples:
        return []

    simple_cfg = ((routing_config.get("lanes") or {}).get("simple") or {})
    current_chars = _coerce_int(simple_cfg.get("max_chars"), 360)
    current_words = _coerce_int(simple_cfg.get("max_words"), 70)
    suggested_chars = min(_percentile([_coerce_int(row.get("char_count"), 0) for row in safe_primary], 0.90), 600)
    suggested_words = min(_percentile([_coerce_int(row.get("word_count"), 0) for row in safe_primary], 0.90), 110)

    suggestions: list[dict[str, Any]] = []
    if suggested_chars > current_chars:
        suggestions.append(
            {
                "key": "smart_model_routing.lanes.simple.max_chars",
                "current": current_chars,
                "suggested": suggested_chars,
                "reason": "safe_primary_spillover_p90_chars",
                "sample_count": len(safe_primary),
            }
        )
    if suggested_words > current_words:
        suggestions.append(
            {
                "key": "smart_model_routing.lanes.simple.max_words",
                "current": current_words,
                "suggested": suggested_words,
                "reason": "safe_primary_spillover_p90_words",
                "sample_count": len(safe_primary),
            }
        )
    return suggestions


def _is_safe_primary_spillover(row: dict[str, Any]) -> bool:
    return (
        row.get("routed_to_primary") is True
        and row.get("lane") is None
        and row.get("routing_reason") is None
        and row.get("complex_signal") is False
        and row.get("protected_signal") is False
        and _coerce_int(row.get("char_count"), 0) > 0
        and _coerce_int(row.get("word_count"), 0) > 0
    )


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[index]


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
