"""Power management utilities for Raspberry Pi battery operation.

Supports low-power idle by shutting off Wi-Fi, Bluetooth, HDMI, and status LEDs,
and lowering the CPU scaling governor between scheduled E-ink display refreshes.

Always starts in an INACTIVE state on boot / power-loss so the user can connect
over Wi-Fi to configure the device without being locked out.
"""

import glob
import json
import logging
import os
import shutil
import socket
import subprocess
import time

logger = logging.getLogger(__name__)

# Snapshot of the hardware state taken before entering low-power mode. It survives a
# power loss during sleep so the next boot can undo exactly what was changed.
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", ".power_save_state.json")
LED_DIR = "/sys/class/leds"
CPU_GOVERNOR_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"


def is_raspberry_pi():
    """Detect whether the current system is a Raspberry Pi running Linux."""
    if os.name != "posix" or not os.path.exists("/proc/cpuinfo"):
        return False
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            if "Raspberry Pi" in content or "BCM" in content:
                return True
        if os.path.exists("/proc/device-tree/model"):
            with open("/proc/device-tree/model", "r", encoding="utf-8", errors="ignore") as f:
                if "Raspberry Pi" in f.read():
                    return True
    except Exception:
        pass
    return False


class PowerManager:
    """Manages power-saving states for battery-powered operation."""

    def __init__(self, device_config=None, dev_mode=False, state_file=DEFAULT_STATE_FILE):
        self.device_config = device_config
        self.dev_mode = dev_mode
        self.is_pi = is_raspberry_pi() and not dev_mode
        self.state_file = state_file

        # Runtime-only state: always False on boot / power-loss for safety
        self.is_active = False
        self.is_in_low_power = False
        self.keep_awake_until = 0.0
        self._saved_state = None

        logger.info(f"PowerManager initialized (is_raspberry_pi={self.is_pi}, dev_mode={self.dev_mode})")
        self.restore_hardware_on_boot()

    def restore_hardware_on_boot(self):
        """Undo any low-power changes left behind by a power loss during sleep.

        systemd-rfkill restores the last rfkill state at boot, so a battery cut while
        asleep would otherwise leave Wi-Fi blocked and the web UI unreachable.
        """
        state = self._load_state()
        if self.is_pi:
            # Wi-Fi is always unblocked, even without a snapshot: reachability is the
            # one thing boot safety must guarantee.
            self.enable_wifi()
            if state:
                logger.warning("Found low-power snapshot from previous run, restoring hardware state.")
                self._restore_hardware(state)
        self._clear_state()

    def get_config(self):
        """Return power save configuration dictionary from device_config."""
        default_config = {
            "awake_duration": 3,
            "awake_unit": "minute",  # "second" or "minute"
            "sleep_duration": 1,
            "sleep_unit": "hour",  # "minute" or "hour"
            "turn_off_wifi": True,
            "turn_off_bluetooth": True,
            "turn_off_hdmi": True,
            "turn_off_led": True,
        }
        if not self.device_config:
            return default_config
        saved = self.device_config.get_config("power_save_config", default={})
        merged = dict(default_config)
        merged.update(saved)
        return merged

    def get_status(self):
        """Return the current runtime power saving state."""
        now = time.time()
        remaining_awake = max(0, int(self.keep_awake_until - now)) if self.keep_awake_until > now else 0
        return {
            "is_active": self.is_active,
            "is_in_low_power": self.is_in_low_power,
            "remaining_awake_seconds": remaining_awake,
            "is_raspberry_pi": self.is_pi,
            "config": self.get_config(),
        }

    def set_active(self, active: bool):
        """Activate or deactivate power saving mode for this runtime session."""
        self.is_active = bool(active)
        logger.info(f"Power saving mode active set to: {self.is_active}")
        if self.is_active:
            ps_cfg = self.get_config()
            awake_mult = 60 if ps_cfg.get("awake_unit") == "minute" else 1
            awake_seconds = max(10, int(ps_cfg.get("awake_duration", 3)) * awake_mult)
            self.keep_awake_until = max(self.keep_awake_until, time.time() + awake_seconds)
        elif self.is_in_low_power:
            self.exit_low_power_mode()

    def request_keep_awake(self, seconds=300):
        """Extend the awake window so the user can continue configuring the web UI."""
        self.keep_awake_until = max(self.keep_awake_until, time.time() + seconds)
        logger.info(f"Keep-awake requested for {seconds}s (until {self.keep_awake_until})")

    # --- Hardware Controls ---

    def _run_cmd(self, cmd_list, check=False):
        """Run a shell command safely."""
        try:
            res = subprocess.run(cmd_list, capture_output=True, text=True, check=check, timeout=10)
            return res.returncode == 0
        except Exception as e:
            logger.debug(f"Command {' '.join(cmd_list)} failed: {e}")
            return False

    def disable_wifi(self):
        """Turn off Wi-Fi on Raspberry Pi."""
        if not self.is_pi:
            logger.info("[Mock] Wi-Fi disabled (Power Save)")
            return True
        logger.info("Disabling Wi-Fi for power save...")
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "block", "wifi"])
        if shutil.which("ip"):
            self._run_cmd(["ip", "link", "set", "wlan0", "down"])
        return True

    def enable_wifi(self):
        """Turn on Wi-Fi on Raspberry Pi."""
        if not self.is_pi:
            logger.info("[Mock] Wi-Fi enabled")
            return True
        logger.info("Enabling Wi-Fi...")
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "unblock", "wifi"])
        if shutil.which("ip"):
            self._run_cmd(["ip", "link", "set", "wlan0", "up"])
        return True

    def wait_for_network(self, timeout=30):
        """Wait until network connectivity is restored after waking Wi-Fi."""
        if not self.is_pi:
            return True

        logger.info(f"Waiting for network connection (timeout={timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Test connectivity using DNS socket lookup
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(2.0)
                s.connect(("1.1.1.1", 53))
                s.close()
                logger.info("Network connected successfully.")
                return True
            except Exception:
                time.sleep(1.5)
        logger.warning("Network connection timed out after wake.")
        return False

    def disable_bluetooth(self):
        """Turn off Bluetooth."""
        if not self.is_pi:
            logger.info("[Mock] Bluetooth disabled")
            return True
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "block", "bluetooth"])
        return True

    def enable_bluetooth(self):
        """Turn on Bluetooth."""
        if not self.is_pi:
            return True
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "unblock", "bluetooth"])
        return True

    def set_hdmi_power(self, enable: bool):
        """Toggle HDMI display port power."""
        if not self.is_pi:
            logger.info(f"[Mock] HDMI power set to {enable}")
            return True
        val = "1" if enable else "0"
        if shutil.which("vcgencmd"):
            self._run_cmd(["vcgencmd", "display_power", val])
        return True

    def get_led_state(self):
        """Return {led_name: {"trigger": ..., "brightness": ...}} for all onboard LEDs."""
        leds = {}
        for led_path in glob.glob(os.path.join(LED_DIR, "*")):
            try:
                trigger = _read_active_trigger(os.path.join(led_path, "trigger"))
                with open(os.path.join(led_path, "brightness")) as f:
                    brightness = f.read().strip()
                leds[os.path.basename(led_path)] = {"trigger": trigger, "brightness": brightness}
            except Exception:
                pass
        return leds

    def turn_off_leds(self):
        """Turn off onboard LEDs. The trigger must be cleared first, otherwise it turns them back on."""
        if not self.is_pi:
            logger.info("[Mock] Status LEDs turned off")
            return True
        for led_path in glob.glob(os.path.join(LED_DIR, "*")):
            _write_sysfs(os.path.join(led_path, "trigger"), "none")
            _write_sysfs(os.path.join(led_path, "brightness"), "0")
        return True

    def restore_leds(self, led_state):
        """Restore LED triggers (e.g. mmc0, heartbeat) and brightness saved by get_led_state()."""
        if not self.is_pi:
            logger.info("[Mock] Status LEDs restored")
            return True
        for name, state in (led_state or {}).items():
            led_path = os.path.join(LED_DIR, name)
            trigger = state.get("trigger")
            if trigger:
                _write_sysfs(os.path.join(led_path, "trigger"), trigger)
            # A non-"none" trigger drives the LED itself; brightness only matters for manual LEDs
            if trigger in (None, "none") and state.get("brightness") is not None:
                _write_sysfs(os.path.join(led_path, "brightness"), state["brightness"])
        return True

    def get_cpu_governor(self):
        """Return the current scaling governor of the first CPU, or None if unavailable."""
        for gov_path in sorted(glob.glob(CPU_GOVERNOR_GLOB)):
            try:
                with open(gov_path) as f:
                    return f.read().strip()
            except Exception:
                pass
        return None

    def set_cpu_governor(self, governor="powersave"):
        """Set CPU scaling governor to save energy while idle."""
        if not self.is_pi:
            logger.info(f"[Mock] CPU governor set to {governor}")
            return True
        for gov_path in glob.glob(CPU_GOVERNOR_GLOB):
            _write_sysfs(gov_path, governor)
        return True

    # --- Hardware state snapshot ---

    def _capture_state(self, config):
        """Record what is about to be changed so it can be undone on wake or next boot."""
        return {
            "wifi_disabled": bool(config.get("turn_off_wifi", True)),
            "bluetooth_disabled": bool(config.get("turn_off_bluetooth", True)),
            "hdmi_disabled": bool(config.get("turn_off_hdmi", True)),
            "leds": self.get_led_state() if self.is_pi and config.get("turn_off_led", True) else None,
            "cpu_governor": self.get_cpu_governor() if self.is_pi else None,
        }

    def _restore_hardware(self, state):
        """Undo the changes recorded in a snapshot (except Wi-Fi, which callers handle)."""
        governor = state.get("cpu_governor")
        if governor:
            self.set_cpu_governor(governor)
        if state.get("bluetooth_disabled"):
            self.enable_bluetooth()
        if state.get("hdmi_disabled"):
            self.set_hdmi_power(True)
        if state.get("leds") is not None:
            self.restore_leds(state["leds"])

    def _save_state(self, state):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp_path = self.state_file + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_file)
        except Exception as e:
            logger.warning(f"Could not save power save state to {self.state_file}: {e}")

    def _load_state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Could not read power save state from {self.state_file}: {e}")
            return None

    def _clear_state(self):
        try:
            os.remove(self.state_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Could not remove power save state {self.state_file}: {e}")

    # --- High-level Power Modes ---

    def enter_low_power_mode(self):
        """Enter deep idle power saving mode."""
        if self.is_in_low_power:
            return
        config = self.get_config()
        logger.info("Entering low-power idle mode...")

        # Persist the snapshot before touching hardware so a power cut mid-way is recoverable
        self._saved_state = self._capture_state(config)
        self._save_state(self._saved_state)

        if config.get("turn_off_led", True):
            self.turn_off_leds()

        if config.get("turn_off_hdmi", True):
            self.set_hdmi_power(False)

        if config.get("turn_off_bluetooth", True):
            self.disable_bluetooth()

        self.set_cpu_governor("powersave")

        if config.get("turn_off_wifi", True):
            self.disable_wifi()

        self.is_in_low_power = True
        logger.info("Low-power idle mode active.")

    def exit_low_power_mode(self):
        """Restore all peripherals and CPU frequency."""
        if not self.is_in_low_power:
            return
        logger.info("Exiting low-power mode, waking peripherals...")

        # Undo what was actually changed on entry, even if the config was edited meanwhile
        state = self._saved_state or self._capture_state(self.get_config())
        if state.get("wifi_disabled"):
            self.enable_wifi()
            self.wait_for_network(timeout=25)

        self._restore_hardware(state)
        if not state.get("cpu_governor"):
            self.set_cpu_governor("ondemand")

        self._saved_state = None
        self._clear_state()
        self.is_in_low_power = False
        logger.info("Peripherals restored to normal mode.")


def _read_active_trigger(trigger_path):
    """Return the active trigger from a sysfs LED trigger file ("none [mmc0] timer" -> "mmc0")."""
    with open(trigger_path) as f:
        for token in f.read().split():
            if token.startswith("[") and token.endswith("]"):
                return token[1:-1]
    return None


def _write_sysfs(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except Exception:
        return False
