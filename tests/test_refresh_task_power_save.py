"""Tests for the power save sleep/wake loop in RefreshTask._run (real thread, short timings)."""
import threading
import time
from unittest.mock import MagicMock

import pytest

import refresh_task as refresh_task_module
from refresh_task import RefreshTask
from utils.power_manager import PowerManager

AWAKE_SECONDS = 0.3
WAIT_TIMEOUT = 5


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """RefreshTask wired to a dev-mode PowerManager, recording loop events in order."""
    config_values = {"timezone": "UTC", "plugin_cycle_interval_seconds": 3600}
    device_config = MagicMock()
    device_config.get_config.side_effect = lambda key=None, default=None: config_values.get(key, default)
    device_config.get_plugin.return_value = None  # stop each refresh before rendering a plugin

    power_manager = PowerManager(device_config=None, dev_mode=True, state_file=str(tmp_path / "state.json"))
    task = RefreshTask(device_config, MagicMock(), power_manager)

    events = []
    first_refresh_check = threading.Event()
    low_power_entered = threading.Event()
    low_power_exited = threading.Event()

    def record(name, original, signal=None):
        def wrapper(*args, **kwargs):
            events.append(name)
            result = original(*args, **kwargs)
            if signal:
                signal.set()
            return result
        return wrapper

    monkeypatch.setattr(power_manager, "enter_low_power_mode",
                        record("enter", power_manager.enter_low_power_mode, low_power_entered))
    monkeypatch.setattr(power_manager, "exit_low_power_mode",
                        record("exit", power_manager.exit_low_power_mode, low_power_exited))

    playlist, plugin_instance = MagicMock(), MagicMock()

    def fake_determine_next_plugin(playlist_manager, latest_refresh, current_dt, force=False):
        events.append(("determine", force))
        first_refresh_check.set()
        return playlist, plugin_instance

    monkeypatch.setattr(task, "_determine_next_plugin", fake_determine_next_plugin)

    playlist_refreshes = []
    real_playlist_refresh = refresh_task_module.PlaylistRefresh

    def fake_playlist_refresh(playlist, plugin_instance, force=False):
        playlist_refreshes.append(force)
        return real_playlist_refresh(playlist, plugin_instance, force=force)

    monkeypatch.setattr(refresh_task_module, "PlaylistRefresh", fake_playlist_refresh)

    def configure(sleep_seconds):
        monkeypatch.setattr(power_manager, "get_awake_seconds", lambda: AWAKE_SECONDS)
        monkeypatch.setattr(power_manager, "get_sleep_seconds", lambda: sleep_seconds)

    h = MagicMock()
    h.task, h.power_manager, h.events, h.playlist_refreshes = task, power_manager, events, playlist_refreshes
    h.first_refresh_check, h.low_power_entered, h.low_power_exited = first_refresh_check, low_power_entered, low_power_exited
    h.configure = configure
    yield h

    task.stop()


def test_sleep_wake_cycle_forces_refresh(harness):
    harness.configure(sleep_seconds=0.3)
    harness.power_manager.set_active(True)
    harness.task.start()

    assert harness.first_refresh_check.wait(WAIT_TIMEOUT)
    harness.task.stop()

    # awake window -> low-power sleep -> wake -> refresh that bypasses the cycle interval
    assert harness.events[:3] == ["enter", "exit", ("determine", True)]
    assert harness.playlist_refreshes[0] is True
    assert harness.power_manager.is_in_low_power is False


def test_inactive_power_save_never_sleeps(harness):
    harness.configure(sleep_seconds=0.3)
    harness.task.start()

    time.sleep(AWAKE_SECONDS * 3)
    harness.task.stop()

    assert "enter" not in harness.events
    assert "exit" not in harness.events


def test_deactivate_during_sleep_wakes_immediately(harness):
    harness.configure(sleep_seconds=60)
    harness.power_manager.set_active(True)
    harness.task.start()
    assert harness.low_power_entered.wait(WAIT_TIMEOUT)

    started = time.time()
    harness.power_manager.set_active(False)
    harness.task.signal_config_change()

    assert harness.low_power_exited.wait(WAIT_TIMEOUT)
    assert time.time() - started < WAIT_TIMEOUT
    assert harness.power_manager.is_in_low_power is False


def test_manual_update_during_awake_window_skips_sleep(harness):
    harness.configure(sleep_seconds=60)
    harness.power_manager.set_active(True)
    harness.power_manager.request_keep_awake(30)  # long awake window
    harness.task.start()

    refresh_action = MagicMock()
    refresh_action.get_plugin_id.return_value = "clock"
    done = threading.Thread(target=harness.task.manual_update, args=(refresh_action,), daemon=True)
    done.start()
    done.join(WAIT_TIMEOUT)

    assert not done.is_alive()
    refresh_action.get_plugin_id.assert_called()
    assert "enter" not in harness.events


def test_stop_during_sleep_restores_hardware(harness):
    harness.configure(sleep_seconds=60)
    harness.power_manager.set_active(True)
    harness.task.start()
    assert harness.low_power_entered.wait(WAIT_TIMEOUT)

    stopper = threading.Thread(target=harness.task.stop, daemon=True)
    stopper.start()
    stopper.join(WAIT_TIMEOUT)

    assert not stopper.is_alive()
    assert harness.events[-1] == "exit"
    assert harness.power_manager.is_in_low_power is False
