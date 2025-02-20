"""Test system log component."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.bootstrap import async_setup_component
from homeassistant.components import system_log
from homeassistant.core import callback

from tests.common import async_capture_events

_LOGGER = logging.getLogger("test_logger")
BASIC_CONFIG = {"system_log": {"max_entries": 2}}


async def get_error_log(hass_ws_client):
    """Fetch all entries from system_log via the API."""
    client = await hass_ws_client()
    await client.send_json({"id": 5, "type": "system_log/list"})

    msg = await client.receive_json()

    assert msg["id"] == 5
    assert msg["success"]
    return msg["result"]


def _generate_and_log_exception(exception, log):
    try:
        raise Exception(exception)
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception(log)


def find_log(logs, level):
    """Return log with specific level."""
    if not isinstance(level, tuple):
        level = (level,)
    log = next(
        (log for log in logs if log["level"] in level and log["name"] != "asyncio"),
        None,
    )
    assert log is not None
    return log


def assert_log(log, exception, message, level):
    """Assert that specified values are in a specific log entry."""
    if not isinstance(message, list):
        message = [message]

    assert log["name"] == "test_logger"
    assert exception in log["exception"]
    assert message == log["message"]
    assert level == log["level"]
    assert "timestamp" in log


class WatchLogErrorHandler(system_log.LogErrorHandler):
    """WatchLogErrorHandler that watches for a message."""

    instances: list[WatchLogErrorHandler] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize HASSQueueListener."""
        super().__init__(*args, **kwargs)
        self.watch_message: str | None = None
        self.watch_event: asyncio.Event | None = asyncio.Event()
        WatchLogErrorHandler.instances.append(self)

    def add_watcher(self, match: str) -> Awaitable:
        """Add a watcher."""
        self.watch_event = asyncio.Event()
        self.watch_message = match
        return self.watch_event.wait()

    def handle(self, record: logging.LogRecord) -> None:
        """Handle a logging record."""
        super().handle(record)
        if record.message in self.watch_message:
            self.watch_event.set()


def get_frame(name):
    """Get log stack frame."""
    return (name, 5, None, None)


async def async_setup_system_log(hass, config) -> WatchLogErrorHandler:
    """Set up the system_log component."""
    WatchLogErrorHandler.instances = []
    with patch(
        "homeassistant.components.system_log.LogErrorHandler", WatchLogErrorHandler
    ):
        await async_setup_component(hass, system_log.DOMAIN, config)
        await hass.async_block_till_done()

    assert len(WatchLogErrorHandler.instances) == 1
    return WatchLogErrorHandler.instances.pop()


async def test_normal_logs(hass, hass_ws_client):
    """Test that debug and info are not logged."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.debug("debug")
    _LOGGER.info("info")

    # Assert done by get_error_log
    logs = await get_error_log(hass_ws_client)
    assert len([msg for msg in logs if msg["level"] in ("DEBUG", "INFO")]) == 0


async def test_exception(hass, hass_ws_client):
    """Test that exceptions are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    _generate_and_log_exception("exception message", "log message")
    log = find_log(await get_error_log(hass_ws_client), "ERROR")
    assert log is not None
    assert_log(log, "exception message", "log message", "ERROR")


async def test_warning(hass, hass_ws_client):
    """Test that warning are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.warning("warning message")

    log = find_log(await get_error_log(hass_ws_client), "WARNING")
    assert_log(log, "", "warning message", "WARNING")


async def test_warning_good_format(hass, hass_ws_client):
    """Test that warning with good format arguments are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.warning("warning message: %s", "test")
    await hass.async_block_till_done()

    log = find_log(await get_error_log(hass_ws_client), "WARNING")
    assert_log(log, "", "warning message: test", "WARNING")


async def test_warning_missing_format_args(hass, hass_ws_client):
    """Test that warning with missing format arguments are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.warning("warning message missing a format arg %s")
    await hass.async_block_till_done()

    log = find_log(await get_error_log(hass_ws_client), "WARNING")
    assert_log(log, "", ["warning message missing a format arg %s"], "WARNING")


async def test_error(hass, hass_ws_client):
    """Test that errors are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    _LOGGER.error("error message")

    log = find_log(await get_error_log(hass_ws_client), "ERROR")
    assert_log(log, "", "error message", "ERROR")


async def test_config_not_fire_event(hass):
    """Test that errors are not posted as events with default config."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    events = []

    @callback
    def event_listener(event):
        """Listen to events of type system_log_event."""
        events.append(event)

    hass.bus.async_listen(system_log.EVENT_SYSTEM_LOG, event_listener)

    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert len(events) == 0


async def test_error_posted_as_event(hass):
    """Test that error are posted as events."""
    watcher = await async_setup_system_log(
        hass, {"system_log": {"max_entries": 2, "fire_event": True}}
    )
    wait_empty = watcher.add_watcher("error message")

    events = async_capture_events(hass, system_log.EVENT_SYSTEM_LOG)

    _LOGGER.error("error message")
    await wait_empty
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert_log(events[0].data, "", "error message", "ERROR")


async def test_critical(hass, hass_ws_client):
    """Test that critical are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    _LOGGER.critical("critical message")

    log = find_log(await get_error_log(hass_ws_client), "CRITICAL")
    assert_log(log, "", "critical message", "CRITICAL")


async def test_critical_with_missing_format_args(hass, hass_ws_client):
    """Test that critical messages with missing format args are logged and retrieved correctly."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    try:
        _LOGGER.critical("critical message %s = %s", "one_but_needs_two")
    except TypeError:
        pass


async def test_remove_older_logs(hass, hass_ws_client):
    """Test that older logs are rotated out."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.error("error message 1")
    _LOGGER.error("error message 2")
    _LOGGER.error("error message 3")
    await hass.async_block_till_done()
    log = await get_error_log(hass_ws_client)
    assert_log(log[0], "", "error message 3", "ERROR")
    assert_log(log[1], "", "error message 2", "ERROR")


def log_msg(nr=2):
    """Log an error at same line."""
    _LOGGER.error("error message %s", nr)


async def test_dedupe_logs(hass, hass_ws_client):
    """Test that duplicate log entries are dedupe."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.error("error message 1")
    log_msg()
    log_msg("2-2")
    _LOGGER.error("error message 3")

    log = await get_error_log(hass_ws_client)
    assert_log(log[0], "", "error message 3", "ERROR")
    assert log[1]["count"] == 2
    assert_log(log[1], "", ["error message 2", "error message 2-2"], "ERROR")

    log_msg()
    log = await get_error_log(hass_ws_client)
    assert_log(log[0], "", ["error message 2", "error message 2-2"], "ERROR")
    assert log[0]["timestamp"] > log[0]["first_occurred"]

    log_msg("2-3")
    log_msg("2-4")
    log_msg("2-5")
    log_msg("2-6")

    log = await get_error_log(hass_ws_client)
    assert_log(
        log[0],
        "",
        [
            "error message 2-2",
            "error message 2-3",
            "error message 2-4",
            "error message 2-5",
            "error message 2-6",
        ],
        "ERROR",
    )


async def test_clear_logs(hass, hass_ws_client):
    """Test that the log can be cleared via a service call."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.error("error message")

    await hass.services.async_call(system_log.DOMAIN, system_log.SERVICE_CLEAR, {})
    await hass.async_block_till_done()
    # Assert done by get_error_log
    await get_error_log(hass_ws_client)


async def test_write_log(hass):
    """Test that error propagates to logger."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    logger = MagicMock()
    with patch("logging.getLogger", return_value=logger) as mock_logging:
        await hass.services.async_call(
            system_log.DOMAIN, system_log.SERVICE_WRITE, {"message": "test_message"}
        )
        await hass.async_block_till_done()
    mock_logging.assert_called_once_with("homeassistant.components.system_log.external")
    assert logger.method_calls[0] == ("error", ("test_message",))


async def test_write_choose_logger(hass):
    """Test that correct logger is chosen."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    with patch("logging.getLogger") as mock_logging:
        await hass.services.async_call(
            system_log.DOMAIN,
            system_log.SERVICE_WRITE,
            {"message": "test_message", "logger": "myLogger"},
        )
        await hass.async_block_till_done()
    mock_logging.assert_called_once_with("myLogger")


async def test_write_choose_level(hass):
    """Test that correct logger is chosen."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()

    logger = MagicMock()
    with patch("logging.getLogger", return_value=logger):
        await hass.services.async_call(
            system_log.DOMAIN,
            system_log.SERVICE_WRITE,
            {"message": "test_message", "level": "debug"},
        )
        await hass.async_block_till_done()
    assert logger.method_calls[0] == ("debug", ("test_message",))


async def test_unknown_path(hass, hass_ws_client):
    """Test error logged from unknown path."""
    await async_setup_component(hass, system_log.DOMAIN, BASIC_CONFIG)
    await hass.async_block_till_done()
    _LOGGER.findCaller = MagicMock(return_value=("unknown_path", 0, None, None))
    _LOGGER.error("error message")
    log = (await get_error_log(hass_ws_client))[0]
    assert log["source"] == ["unknown_path", 0]


async def async_log_error_from_test_path(hass, path, watcher):
    """Log error while mocking the path."""
    call_path = "internal_path.py"
    with patch.object(
        _LOGGER, "findCaller", MagicMock(return_value=(call_path, 0, None, None))
    ), patch(
        "traceback.extract_stack",
        MagicMock(
            return_value=[
                get_frame("main_path/main.py"),
                get_frame(path),
                get_frame(call_path),
                get_frame("venv_path/logging/log.py"),
            ]
        ),
    ):
        wait_empty = watcher.add_watcher("error message")
        _LOGGER.error("error message")
        await wait_empty


async def test_homeassistant_path(hass, hass_ws_client):
    """Test error logged from Home Assistant path."""

    with patch(
        "homeassistant.components.system_log.HOMEASSISTANT_PATH",
        new=["venv_path/homeassistant"],
    ):
        watcher = await async_setup_system_log(hass, BASIC_CONFIG)
        await async_log_error_from_test_path(
            hass, "venv_path/homeassistant/component/component.py", watcher
        )
        log = (await get_error_log(hass_ws_client))[0]
    assert log["source"] == ["component/component.py", 5]


async def test_config_path(hass, hass_ws_client):
    """Test error logged from config path."""

    with patch.object(hass.config, "config_dir", new="config"):
        watcher = await async_setup_system_log(hass, BASIC_CONFIG)

        await async_log_error_from_test_path(
            hass, "config/custom_component/test.py", watcher
        )
        log = (await get_error_log(hass_ws_client))[0]
    assert log["source"] == ["custom_component/test.py", 5]
