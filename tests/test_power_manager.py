import time
import pytest
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


# --- Hardware restore (fake sysfs, Pi mode) ---

import json
from datetime import datetime, timezone

import utils.power_manager as pm_module


def _fake_sysfs(tmp_path, monkeypatch, trigger="none [mmc0] timer", governor="ondemand"):
    led = tmp_path / "leds" / "ACT"
    led.mkdir(parents=True)
    (led / "trigger").write_text(trigger)
    (led / "brightness").write_text("0")
    cpu = tmp_path / "cpu0"
    cpu.mkdir()
    (cpu / "scaling_governor").write_text(governor)
    monkeypatch.setattr(pm_module, "LED_DIR", str(tmp_path / "leds"))
    monkeypatch.setattr(pm_module, "CPU_GOVERNOR_GLOB", str(tmp_path / "cpu*" / "scaling_governor"))
    monkeypatch.setattr(pm_module, "RFKILL_DIR", str(tmp_path / "rfkill"))
    monkeypatch.setattr(pm_module, "is_raspberry_pi", lambda: True)
    monkeypatch.setattr(pm_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands = []
    monkeypatch.setattr(PowerManager, "_run_cmd", lambda self, cmd, check=False: commands.append(cmd) or True)
    monkeypatch.setattr(PowerManager, "wait_for_network", lambda self, timeout=30: True)
    return led, cpu, commands


def test_boot_always_unblocks_wifi_without_snapshot(tmp_path, monkeypatch):
    _, _, commands = _fake_sysfs(tmp_path, monkeypatch)
    PowerManager(state_file=str(tmp_path / "state.json"))

    assert ["rfkill", "unblock", "wifi"] in commands
    assert ["rfkill", "unblock", "bluetooth"] not in commands


def test_boot_restores_snapshot_left_by_power_loss(tmp_path, monkeypatch):
    led, cpu, commands = _fake_sysfs(tmp_path, monkeypatch, trigger="[none] mmc0", governor="powersave")
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "wifi_disabled": True,
        "bluetooth_disabled": True,
        "hdmi_disabled": False,
        "leds": {"ACT": {"trigger": "mmc0", "brightness": "0"}},
        "cpu_governor": "ondemand",
    }))

    PowerManager(state_file=str(state_file))

    assert ["rfkill", "unblock", "wifi"] in commands
    assert ["rfkill", "unblock", "bluetooth"] in commands
    assert (led / "trigger").read_text() == "mmc0"
    assert (cpu / "scaling_governor").read_text() == "ondemand"
    assert not state_file.exists()


def test_led_trigger_restored_after_wake(tmp_path, monkeypatch):
    led, cpu, _ = _fake_sysfs(tmp_path, monkeypatch)
    state_file = tmp_path / "state.json"
    pm = PowerManager(state_file=str(state_file))

    pm.enter_low_power_mode()
    assert (led / "trigger").read_text() == "none"
    assert (led / "brightness").read_text() == "0"
    assert (cpu / "scaling_governor").read_text() == "powersave"
    assert state_file.exists()

    pm.exit_low_power_mode()
    # Trigger restored instead of forcing brightness=1 (which left the LED solid on)
    assert (led / "trigger").read_text() == "mmc0"
    assert (led / "brightness").read_text() == "0"
    assert (cpu / "scaling_governor").read_text() == "ondemand"
    assert not state_file.exists()


# --- Forced refresh after wake ---

def test_determine_next_plugin_force_skips_cycle_interval():
    from refresh_task import RefreshTask

    device_config = MagicMock()
    device_config.get_config.return_value = 3600
    task = RefreshTask(device_config, MagicMock())

    now = datetime.now(timezone.utc)
    playlist = MagicMock()
    playlist.plugins = [MagicMock()]
    playlist_manager = MagicMock()
    playlist_manager.determine_active_playlist.return_value = playlist
    latest_refresh = MagicMock()
    latest_refresh.get_refresh_datetime.return_value = now  # refreshed just now

    assert task._determine_next_plugin(playlist_manager, latest_refresh, now) == (None, None)
    _, plugin = task._determine_next_plugin(playlist_manager, latest_refresh, now, force=True)
    assert plugin is playlist.get_next_plugin.return_value


# --- I4: Bluetooth left alone if the user had it off ---

def _fake_rfkill(tmp_path, monkeypatch, bluetooth_soft):
    radio = tmp_path / "rfkill" / "rfkill0"
    radio.mkdir(parents=True)
    (radio / "type").write_text("bluetooth")
    (radio / "soft").write_text(bluetooth_soft)
    monkeypatch.setattr(pm_module, "RFKILL_DIR", str(tmp_path / "rfkill"))


@pytest.mark.parametrize("soft, should_toggle", [("0", True), ("1", False)])
def test_bluetooth_only_restored_if_it_was_on(tmp_path, monkeypatch, soft, should_toggle):
    _, _, commands = _fake_sysfs(tmp_path, monkeypatch)
    _fake_rfkill(tmp_path, monkeypatch, soft)
    pm = PowerManager(state_file=str(tmp_path / "state.json"))

    pm.enter_low_power_mode()
    pm.exit_low_power_mode()

    assert (["rfkill", "block", "bluetooth"] in commands) is should_toggle
    assert (["rfkill", "unblock", "bluetooth"] in commands) is should_toggle


# --- I5: no `ip link`, retry Wi-Fi wake ---

def test_wifi_uses_rfkill_only_and_retries_wake(tmp_path, monkeypatch):
    _, _, commands = _fake_sysfs(tmp_path, monkeypatch)
    results = iter([False, True])
    monkeypatch.setattr(PowerManager, "wait_for_network", lambda self, timeout=30: next(results))
    pm = PowerManager(state_file=str(tmp_path / "state.json"))
    commands.clear()

    pm.enter_low_power_mode()
    pm.exit_low_power_mode()

    assert not any(cmd[0] == "ip" for cmd in commands)
    assert commands.count(["rfkill", "unblock", "wifi"]) == 2
    assert ["nmcli", "radio", "wifi", "on"] in commands


# --- I6: real connectivity check ---

def test_wait_for_network_uses_dns_and_tcp(monkeypatch):
    monkeypatch.setattr(pm_module, "is_raspberry_pi", lambda: True)
    monkeypatch.setattr(PowerManager, "restore_hardware_on_boot", lambda self: None)
    calls = []

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pm_module.socket, "create_connection", lambda addr, timeout: calls.append(addr) or FakeConn())
    pm = PowerManager()

    assert pm.wait_for_network(timeout=1) is True
    assert calls == [pm_module.CONNECTIVITY_CHECK_HOST]
