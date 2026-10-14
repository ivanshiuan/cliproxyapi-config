"""The test session must be unable to reach the real LINE API.

Guard lives in ``tests/conftest.py::pytest_configure``. These tests pin it: they
only pass when the guard works, including on a machine whose environment carries
live ``LINE_CHANNEL_*`` credentials.
"""

from __future__ import annotations

from restaurant_api.config import get_settings
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.integrations.line.messenger import get_messenger, reset_messenger


def test_line_credentials_are_blank_during_tests() -> None:
    settings = get_settings()
    assert settings.line_channel_access_token == ""
    assert settings.line_channel_secret == ""


def test_default_messenger_is_the_in_memory_stub() -> None:
    reset_messenger()
    try:
        assert isinstance(get_messenger(), StubLineMessenger)
    finally:
        reset_messenger()
