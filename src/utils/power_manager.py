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
RFKILL_DIR = "/sys/class/rfkill"
# Host used to confirm real connectivity (DNS + TCP) after Wi-Fi wakes up
CONNECTIVITY_CHECK_HOST = ("one.one.one.one", 443)
# Wi-Fi reconnect alone takes ~10-25 s, so shorter awake windows leave no time for web access
MIN_AWAKE_SECONDS = 60
MIN_SLEEP_SECONDS = 60
BOOT_CONFIG_PATHS = ("/boot/firmware/config.txt", "/boot/config.txt")


def is_full_kms():
    """Return True if the full KMS driver (vc4-kms-v3d) is enabled.

    Under full KMS the firmware no longer owns the display, so `vcgencmd display_power`
    has no effect (Bookworm default). The fake-KMS overlay (vc4-fkms-v3d) still supports it.
    """
    for path in BOOT_CONFIG_PATHS:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line.startswith("dtoverlay=") and line.split("=", 1)[1].split(",")[0].strip() == "vc4-kms-v3d":
                        return True
            return False
        except OSError:
            continue
    return False
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
        self.hdmi_control_supported = not (self.is_pi and is_full_kms())
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
            "hdmi_control_supported": self.hdmi_control_supported,
            "config": self.get_config(),
        }

    def get_awake_seconds(self):
        """Configured awake window in seconds (at least MIN_AWAKE_SECONDS)."""
        cfg = self.get_config()
        mult = 60 if cfg.get("awake_unit") == "minute" else 1
        return max(MIN_AWAKE_SECONDS, int(cfg.get("awake_duration", 3)) * mult)

    def get_sleep_seconds(self):
        """Configured low-power sleep in seconds (at least MIN_SLEEP_SECONDS)."""
        cfg = self.get_config()
        mult = 3600 if cfg.get("sleep_unit") == "hour" else 60
        return max(MIN_SLEEP_SECONDS, int(cfg.get("sleep_duration", 1)) * mult)

    def set_active(self, active: bool):
        """Activate or deactivate power saving mode for this runtime session."""
        self.is_active = bool(active)
        logger.info(f"Power saving mode active set to: {self.is_active}")
        if self.is_active:
            self.keep_awake_until = max(self.keep_awake_until, time.time() + self.get_awake_seconds())
        else:
            self.keep_awake_until = 0.0
            if self.is_in_low_power:
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
        # rfkill only: taking wlan0 down with `ip link` confuses NetworkManager on Bookworm
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "block", "wifi"])
        return True

    def enable_wifi(self):
        """Turn on Wi-Fi on Raspberry Pi."""
        if not self.is_pi:
            logger.info("[Mock] Wi-Fi enabled")
            return True
        logger.info("Enabling Wi-Fi...")
        if shutil.which("rfkill"):
            self._run_cmd(["rfkill", "unblock", "wifi"])
        return True

    def wake_wifi(self, timeout=25, attempts=3):
        """Unblock Wi-Fi and wait for connectivity, retrying the unblock if it does not come back."""
        for attempt in range(1, attempts + 1):
            self.enable_wifi()
            if self.wait_for_network(timeout=timeout):
                return True
            logger.warning(f"Wi-Fi did not reconnect (attempt {attempt}/{attempts}).")
            if shutil.which("nmcli"):
                self._run_cmd(["nmcli", "radio", "wifi", "on"])
        logger.error("Wi-Fi failed to reconnect after wake; continuing without network.")
        return False

    def wait_for_network(self, timeout=30):
        """Wait until DNS resolves and a TCP connection succeeds after waking Wi-Fi."""
        if not self.is_pi:
            return True

        logger.info(f"Waiting for network connection (timeout={timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                # create_connection resolves the hostname (DNS) and opens a real TCP connection,
                # unlike a UDP connect() which only checks that a route exists
                with socket.create_connection(CONNECTIVITY_CHECK_HOST, timeout=3.0):
                    pass
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

    def is_bluetooth_blocked(self):
        """Return True if every Bluetooth radio is already soft-blocked (e.g. disabled by the user)."""
        radios = []
        for rfkill_path in glob.glob(os.path.join(RFKILL_DIR, "*")):
            try:
                with open(os.path.join(rfkill_path, "type")) as f:
                    if f.read().strip() != "bluetooth":
                        continue
                with open(os.path.join(rfkill_path, "soft")) as f:
                    radios.append(f.read().strip() == "1")
            except Exception:
                pass
        return bool(radios) and all(radios)

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
        if not self.hdmi_control_supported:
            logger.debug("Skipping HDMI power control: not supported under full KMS (vc4-kms-v3d)")
            return False
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
            # Only record Bluetooth as ours to restore if it was on; never re-enable a radio the user turned off
            "bluetooth_disabled": bool(config.get("turn_off_bluetooth", True))
                                  and not (self.is_pi and self.is_bluetooth_blocked()),
            "hdmi_disabled": bool(config.get("turn_off_hdmi", True)) and self.hdmi_control_supported,
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

        if self._saved_state["hdmi_disabled"]:
            self.set_hdmi_power(False)

        if self._saved_state["bluetooth_disabled"]:
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
            self.wake_wifi(timeout=25)

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
