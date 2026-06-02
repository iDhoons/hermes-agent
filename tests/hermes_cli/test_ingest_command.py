"""Tests for the /ingest slash command prompt builder and CLI dispatch."""

from unittest.mock import MagicMock, patch

from cli import HermesCLI
from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command, telegram_bot_commands
from hermes_cli.ingest_command import INGEST_USAGE, build_ingest_prompt


def test_ingest_registered_for_gateway_and_telegram_menu():
    cmd = resolve_command("ingest")

    assert cmd is not None
    assert cmd.name == "ingest"
    assert cmd.args_hint == "<source>"
    assert "ingest" in GATEWAY_KNOWN_COMMANDS
    assert "ingest" in {name for name, _description in telegram_bot_commands()}


def test_build_ingest_prompt_includes_source_and_operational_guardrails():
    prompt = build_ingest_prompt("https://example.com/post")

    assert "https://example.com/post" in prompt
    assert "Obsidian" in prompt
    assert "현재 시간" in prompt
    assert "TL;DR" in prompt
    assert "적용 후보" in prompt
    assert "승인" in prompt


def test_build_ingest_prompt_returns_empty_for_blank_source():
    assert build_ingest_prompt("  \n\t ") == ""


def test_cli_ingest_queues_built_prompt_as_next_turn():
    cli = object.__new__(HermesCLI)
    cli._pending_input = MagicMock()

    assert HermesCLI.process_command(cli, "/ingest https://example.com/post") is True

    queued = cli._pending_input.put.call_args.args[0]
    assert "https://example.com/post" in queued
    assert "Obsidian" in queued
    assert not queued.startswith("/ingest")


def test_cli_ingest_without_source_prints_usage():
    cli = object.__new__(HermesCLI)
    cli._pending_input = MagicMock()

    with patch("cli._cprint") as mock_print:
        assert HermesCLI.process_command(cli, "/ingest") is True

    cli._pending_input.put.assert_not_called()
    printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
    assert INGEST_USAGE in printed
