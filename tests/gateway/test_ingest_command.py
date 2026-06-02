"""Gateway dispatch tests for the /ingest slash command."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from hermes_cli.ingest_command import INGEST_USAGE


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = SimpleNamespace(send=AsyncMock(), _pending_messages={})
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._update_prompt_pending = {}
    runner._voice_mode = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner._is_user_authorized = lambda _source: True
    return runner


@pytest.mark.asyncio
async def test_gateway_ingest_rewrites_to_agent_prompt(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    seen = {}

    async def _capture(event, source, _quick_key, _run_generation):
        seen["text"] = event.text
        return ""

    runner._handle_message_with_agent = _capture  # noqa: SLF001
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )

    result = await runner._handle_message(_make_event("/ingest https://example.com/post"))

    assert result == ""
    assert "https://example.com/post" in seen["text"]
    assert "Obsidian" in seen["text"]
    assert not seen["text"].startswith("/ingest")


@pytest.mark.asyncio
async def test_gateway_ingest_without_source_returns_usage(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(
        side_effect=AssertionError("blank /ingest should not reach the agent")
    )
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )

    result = await runner._handle_message(_make_event("/ingest"))

    assert result == INGEST_USAGE
    runner._handle_message_with_agent.assert_not_called()
