"""Fixtures: a hermetic temp vault+home and an injected probe executor.

The default shipped registry is exercised against temp roots, so the tests
validate the *real* registry plus path expansion without touching the live
vault, network, or systemd.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from context_packet.probes import ProbeRunner


@pytest.fixture
def clock() -> datetime:
    return datetime.now(timezone.utc)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def temp_env(tmp_path):
    """Build temp vault+home matching the default registry's expected paths.

    Returns (vault_root, home_root). The *today* daily note is intentionally
    NOT created, to exercise the missing-evidence warning for daily-focus.
    """
    vault = tmp_path / "vault"
    home = tmp_path / "home"

    _write(home / ".claude/infra/operating-model.md", "# operating model\nrouting.\n")
    _write(home / ".local/share/tmux-today-briefing/current.md",
           "# Today\n- floow interviews\n- ops check\n")
    _write(vault / "Projects/Floow/Floow.md", "# Floow\nproduct intent.\n")
    _write(vault / "_AgentData/memory/long-term.md", "# long term\npaperclip active.\n")
    _write(vault / "_Wiki/infrastructure/linux-operations-guide.md", "# linux ops\n")
    _write(vault / "_Wiki/.data/ops-watch/current.json",
           json.dumps({"overall_status": "ok", "check_date": "2026-06-25",
                       "generated_at": "2026-06-25T00:00:00Z"}))
    _write(vault / "_AgentData/v2/reports/facts-watch/current.json",
           json.dumps({"schema_version": 2, "generated_at": "2026-06-25T00:00:00Z",
                       "findings": []}))
    return str(vault), str(home)


@pytest.fixture
def fake_exec():
    """Injected executor: canned outputs for the only two allowlisted shapes."""
    calls: list[list[str]] = []

    def _exec(argv, timeout):
        calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return 0, "active", ""
        if argv and argv[0] == "curl":
            return 0, "200", ""
        raise AssertionError(f"unexpected exec: {argv!r}")

    _exec.calls = calls
    return _exec


@pytest.fixture
def runner(clock, fake_exec):
    return ProbeRunner(now=clock, executor=fake_exec)
