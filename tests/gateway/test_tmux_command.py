"""Tests for the /tmux gateway command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text: str = "/tmux fix the test") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.DISCORD,
            chat_id="thread-1",
            thread_id="thread-1",
            user_id="user-1",
            user_name="Ada",
        ),
        message_id="msg-1",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._background_tasks = set()
    return runner


def test_tmux_is_gateway_command():
    from hermes_cli.commands import is_gateway_known_command, resolve_command

    cmd = resolve_command("tmux")
    assert cmd is not None
    assert cmd.gateway_only is True
    assert is_gateway_known_command("tmux")
    assert resolve_command("tmux_codex").gateway_only is True
    assert resolve_command("tmux_claude").gateway_only is True
    assert is_gateway_known_command("tmux_codex")
    assert is_gateway_known_command("tmux_claude")
    assert resolve_command("codex") is None
    assert resolve_command("claude") is None


def test_parse_tmux_start_args():
    from gateway.tmux_jobs import parse_start_args

    spec = parse_start_args("ship it")
    assert (spec.agent, spec.prompt, spec.effort) == ("codex", "ship it", "")
    spec = parse_start_args("--claude --xhigh review this")
    assert (spec.agent, spec.prompt, spec.effort) == ("claude", "review this", "xhigh")
    spec = parse_start_args("--agent claude --effort high review this")
    assert (spec.agent, spec.prompt, spec.effort) == ("claude", "review this", "high")
    spec = parse_start_args("--agent cx --model gpt-5.4 review this")
    assert (spec.agent, spec.prompt, spec.model) == ("codex", "review this", "gpt-5.4")
    spec = parse_start_args("--xhigh ship it", default_agent="claude")
    assert (spec.agent, spec.prompt, spec.effort) == ("claude", "ship it", "xhigh")
    spec = parse_start_args("--xhigh 작업", default_agent="codex")
    assert (spec.agent, spec.prompt, spec.effort) == ("codex", "작업", "xhigh")
    spec = parse_start_args("--max 작업", default_agent="claude")
    assert (spec.agent, spec.prompt, spec.effort) == ("claude", "작업", "max")
    with pytest.raises(ValueError, match="Codex effort supports"):
        parse_start_args("--max 작업", default_agent="codex")


def test_agent_command_args_maps_effort_to_each_cli(monkeypatch):
    from gateway.tmux_jobs import agent_command_args

    monkeypatch.setattr("gateway.tmux_jobs.shutil.which", lambda name: None)

    assert agent_command_args("claude", effort="xhigh") == [
        "claude",
        "--permission-mode",
        "auto",
        "--effort",
        "xhigh",
    ]
    assert agent_command_args("codex", effort="xhigh") == [
        "cx",
        "-c",
        'model_reasoning_effort="xhigh"',
    ]
    assert agent_command_args("codex", model="gpt-5.4") == ["cx", "--model", "gpt-5.4"]


def test_agent_command_args_prefers_ccd_for_claude(monkeypatch):
    from gateway.tmux_jobs import agent_command_args

    monkeypatch.setattr("gateway.tmux_jobs.shutil.which", lambda name: "/usr/local/bin/ccd")

    assert agent_command_args("claude", effort="max", model="opus") == [
        "ccd",
        "--model",
        "opus",
        "--effort",
        "max",
    ]


def test_classify_tmux_status_from_pulse_and_report():
    from gateway.tmux_jobs import classify_tmux_status

    assert classify_tmux_status(
        agent="codex",
        pulse_source="codex",
        pulse_reason="codex:active:spinner-title",
        pane_tail="",
    ) == "run"
    assert classify_tmux_status(
        agent="codex",
        pulse_source="codex",
        pulse_reason="codex:waiting:approval-or-input",
        pane_tail="",
    ) == "ask"
    assert classify_tmux_status(
        agent="codex",
        pulse_source="codex",
        pulse_reason="codex:done:prompt-after-active",
        pane_tail="",
    ) == "ok"
    assert classify_tmux_status(
        agent="claude",
        pulse_source="",
        pulse_reason="",
        pane_tail="<HERMES_REPORT>\nstatus: failed\nsummary: blocked\n</HERMES_REPORT>",
    ) == "failed"


def test_tmux_manager_reports_dead_pane(monkeypatch, tmp_path):
    from gateway.tmux_jobs import TmuxJobRecord, TmuxJobRegistry

    manager = TmuxJobRegistry(home=tmp_path)
    record = TmuxJobRecord(
        id="tmux_1",
        agent="codex",
        prompt="fix login",
        pane_id="%42",
        window_name="cx-000001-fix-login",
        tmux_session="hermes",
        workdir="/repo",
        platform="discord",
        chat_id="thread-1",
    )
    monkeypatch.setattr(manager, "_pane_exists", lambda _pane_id: False)

    snapshot = manager.poll(record)

    assert snapshot.status == "dead"
    assert snapshot.summary == "tmux pane is no longer available."


def test_tmux_manager_uses_buffer_for_prompt(monkeypatch, tmp_path):
    from gateway import tmux_jobs

    calls = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["tmux", "has-session"]:
            return Result(0)
        if args[:2] == ["tmux", "new-window"]:
            return Result(0, "%42\n")
        return Result(0)

    monkeypatch.setattr(tmux_jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux_jobs.time, "sleep", lambda _seconds: None)

    manager = tmux_jobs.TmuxJobRegistry(home=tmp_path)
    record = manager.start_job(
        agent="codex",
        prompt="fix login; rm -rf / should never be shell args",
        effort="xhigh",
        workdir=str(tmp_path),
        platform="discord",
        chat_id="thread-1",
    )

    assert record.pane_id == "%42"
    flat_args = " ".join(" ".join(args) for args, _kwargs in calls)
    assert "fix login; rm -rf / should never be shell args" not in flat_args
    assert 'model_reasoning_effort="xhigh"' in flat_args
    buffer_calls = [kwargs.get("input") for args, kwargs in calls if args[:2] == ["tmux", "load-buffer"]]
    assert buffer_calls
    assert "fix login; rm -rf / should never be shell args" in buffer_calls[0]
    assert any(args[:2] == ["tmux", "paste-buffer"] for args, _kwargs in calls)
    assert any(args[:2] == ["tmux", "send-keys"] and args[-1] == "Enter" for args, _kwargs in calls)


@pytest.mark.asyncio
async def test_tmux_handler_starts_default_codex_job(monkeypatch, tmp_path):
    from gateway.tmux_jobs import TmuxJobRecord

    runner = _make_runner()
    started = {}

    class Manager:
        def start_job(self, **kwargs):
            started.update(kwargs)
            return TmuxJobRecord(
                id="tmux_1",
                agent=kwargs["agent"],
                prompt=kwargs["prompt"],
                pane_id="%42",
                window_name="cx-000001-fix",
                tmux_session="hermes",
                workdir=kwargs["workdir"],
                platform=kwargs["platform"],
                chat_id=kwargs["chat_id"],
                effort=kwargs.get("effort", ""),
                model=kwargs.get("model", ""),
            )

    runner._tmux_jobs = Manager()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    result = await runner._handle_tmux_command(_make_event("/tmux_codex --xhigh fix the test"), agent="codex")

    assert started["agent"] == "codex"
    assert started["prompt"] == "fix the test"
    assert started["effort"] == "xhigh"
    assert started["workdir"] == str(tmp_path)
    assert "tmux attach -t hermes" in result
    assert "effort=xhigh" in result
    assert "/tmux read tmux_1" in result


@pytest.mark.asyncio
async def test_tmux_handler_rejects_codex_max_effort():
    runner = _make_runner()

    result = await runner._handle_tmux_command(_make_event("/tmux_codex --max fix the test"), agent="codex")

    assert result == "Codex effort supports low, medium, high, or xhigh"


@pytest.mark.asyncio
async def test_tmux_watcher_posts_status_change_to_origin_thread():
    from gateway.tmux_jobs import (
        TmuxJobRecord,
        TmuxJobSnapshot,
        TmuxJobStatusChange,
    )

    record = TmuxJobRecord(
        id="tmux_1",
        agent="codex",
        prompt="fix login",
        pane_id="%42",
        window_name="cx-000001-fix-login",
        tmux_session="hermes",
        workdir="/repo",
        platform="discord",
        chat_id="thread-1",
        thread_id="thread-1",
        status="ask",
    )
    change = TmuxJobStatusChange(
        record=record,
        previous_status="run",
        snapshot=TmuxJobSnapshot(
            status="ask",
            marker="ask:test",
            pane_tail="raw screen should not be forwarded",
            summary="status: ask\nsummary: choose the migration path\nnext: reply with A or B",
        ),
    )
    marked = []

    class Manager:
        def poll_all(self):
            return [change]

        def mark_notified(self, job_id, status):
            marked.append((job_id, status))

    runner = _make_runner()
    runner._tmux_jobs = Manager()
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {Platform.DISCORD: adapter}

    sent = await runner._run_tmux_job_watcher_once()

    assert sent == 1
    adapter.send.assert_awaited_once()
    args, kwargs = adapter.send.await_args
    assert args[0] == "thread-1"
    assert "tmux job `tmux_1` needs user input" in args[1]
    assert "choose the migration path" in args[1]
    assert "raw screen should not be forwarded" not in args[1]
    assert kwargs["metadata"] == {"thread_id": "thread-1", "non_conversational": True}
    assert marked == [("tmux_1", "ask")]


@pytest.mark.asyncio
async def test_tmux_watcher_suppresses_same_status_marker_duplicate():
    from gateway.tmux_jobs import (
        TmuxJobRecord,
        TmuxJobSnapshot,
        TmuxJobStatusChange,
    )

    record = TmuxJobRecord(
        id="tmux_1",
        agent="codex",
        prompt="fix login",
        pane_id="%42",
        window_name="cx-000001-fix-login",
        tmux_session="hermes",
        workdir="/repo",
        platform="discord",
        chat_id="thread-1",
        thread_id="thread-1",
        status="ok",
        last_notified_status="ok",
        last_notified_marker="ok:abc",
    )
    change = TmuxJobStatusChange(
        record=record,
        previous_status="run",
        snapshot=TmuxJobSnapshot(
            status="ok",
            marker="ok:abc",
            pane_tail="",
            summary="status: ok\nsummary: done",
        ),
    )

    class Manager:
        def poll_all(self):
            return [change]

        def mark_notified(self, job_id, status):
            raise AssertionError("duplicate notification should not be marked")

    runner = _make_runner()
    runner._tmux_jobs = Manager()
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {Platform.DISCORD: adapter}

    sent = await runner._run_tmux_job_watcher_once()

    assert sent == 0
    adapter.send.assert_not_called()
