from gateway.config import Platform
from gateway.run import _prepare_gateway_status_message


def _should_send_status_callback_message(platform, event_type, message):
    return _prepare_gateway_status_message(platform, event_type, message) is not None


def test_telegram_lifecycle_status_messages_are_suppressed():
    assert not _should_send_status_callback_message(
        Platform.TELEGRAM,
        "lifecycle",
        "⏳ Retrying in 2.0s (attempt 1/3)...",
    )


def test_telegram_context_pressure_status_still_delivered():
    assert _should_send_status_callback_message(
        Platform.TELEGRAM,
        "context_pressure",
        "⚠️ Context window is getting full.",
    )


def test_discord_provider_retry_status_messages_are_suppressed():
    assert not _should_send_status_callback_message(
        Platform.DISCORD,
        "lifecycle",
        "⏳ Retrying in 2.0s (attempt 1/3)...",
    )


def test_discord_non_provider_status_messages_still_delivered():
    assert _should_send_status_callback_message(
        Platform.DISCORD,
        "context_pressure",
        "⚠️ Context window is getting full.",
    )
