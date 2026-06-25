"""Phase 1 shadow comparison: divergence signal + read-only on the live view."""

from __future__ import annotations

from pathlib import Path

from context_packet.shadow import (
    SHADOW_SCHEMA_VERSION,
    build_shadow_comparison,
    render_shadow_markdown,
)


def _shadow(domain, temp_env, clock, runner):
    vault, home = temp_env
    return build_shadow_comparison(
        domain, vault_root=vault, home_root=home, now=clock, probe_runner=runner)


def test_daily_focus_shadow_flags_unconfirmed_evidence(temp_env, clock, runner):
    comp = _shadow("daily-focus", temp_env, clock, runner)
    assert comp["mode"] == "shadow"
    assert comp["schema_version"] == SHADOW_SCHEMA_VERSION
    c = comp["comparison"]
    # live briefing fixture has content -> a live view IS present...
    assert c["live_view_present"] is True
    # ...yet PIM/calendar are command_plans and today's note is missing,
    # so the packet flags divergence rather than trusting the live view.
    assert c["divergence"] is True
    unconfirmed = {u["name"] for u in c["unconfirmed_evidence"]}
    assert {"pim_today_tasks", "calendar_today"} <= unconfirmed
    # today_daily_note is missing -> also unconfirmed (failed)
    assert "today_daily_note" in unconfirmed


def test_shadow_does_not_modify_live_view(temp_env, clock, runner):
    vault, home = temp_env
    live = Path(home) / ".local/share/tmux-today-briefing/current.md"
    before = (live.read_text(), live.stat().st_mtime)
    _shadow("daily-focus", temp_env, clock, runner)
    after = (live.read_text(), live.stat().st_mtime)
    assert before == after  # diff-only: the live view is never written


def test_shadow_no_divergence_when_all_probes_ok(temp_env, clock, runner):
    # infra.hermes: fake executor returns active; json views exist -> all ok.
    comp = _shadow("infra.hermes", temp_env, clock, runner)
    c = comp["comparison"]
    assert c["unconfirmed_evidence"] == []
    assert c["divergence"] is False
    assert "agree" in c["note"]


def test_shadow_markdown_renders(temp_env, clock, runner):
    md = render_shadow_markdown(_shadow("daily-focus", temp_env, clock, runner))
    for needle in ("Shadow comparison", "Live view", "Evidence", "Verdict"):
        assert needle in md
    assert "not modified" in md  # the read-only guarantee is stated to the reader
