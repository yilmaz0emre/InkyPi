import time
from unittest.mock import MagicMock
from utils.power_manager import PowerManager


def test_power_manager_initial_state():
    """Verify that power saving mode is strictly inactive by default on startup/boot."""
    mock_config = MagicMock()
    mock_config.get_config.return_value = {}
    pm = PowerManager(device_config=mock_config, dev_mode=True)

    # Boot safety check
    assert pm.is_active is False
    assert pm.is_in_low_power is False
    assert pm.keep_awake_until == 0.0


def test_power_manager_defaults():
    pm = PowerManager(device_config=None, dev_mode=True)
    cfg = pm.get_config()

    assert cfg["awake_duration"] == 3
    assert cfg["awake_unit"] == "minute"
    assert cfg["sleep_duration"] == 1
    assert cfg["sleep_unit"] == "hour"
    assert cfg["turn_off_wifi"] is True
    assert cfg["turn_off_bluetooth"] is True
    assert cfg["turn_off_hdmi"] is True
    assert cfg["turn_off_led"] is True


def test_power_manager_set_active_and_keep_awake():
    mock_config = MagicMock()
    mock_config.get_config.return_value = {
        "awake_duration": 5,
        "awake_unit": "minute",
    }
    pm = PowerManager(device_config=mock_config, dev_mode=True)

    # Activate
    pm.set_active(True)
    assert pm.is_active is True
    # Should automatically assign initial awake window
    assert pm.keep_awake_until > time.time()

    status = pm.get_status()
    assert status["is_active"] is True
    assert status["remaining_awake_seconds"] > 0

    # Request keep awake extension
    initial_awake = pm.keep_awake_until
    pm.request_keep_awake(600)
    assert pm.keep_awake_until >= initial_awake

    # Deactivate
    pm.set_active(False)
    assert pm.is_active is False


def test_power_manager_low_power_transitions_in_dev_mode():
    pm = PowerManager(device_config=None, dev_mode=True)
    assert pm.is_in_low_power is False

    pm.enter_low_power_mode()
    assert pm.is_in_low_power is True

    pm.exit_low_power_mode()
    assert pm.is_in_low_power is False
