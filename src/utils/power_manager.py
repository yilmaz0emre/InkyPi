"""Power management utilities for Raspberry Pi battery operation.

Supports low-power idle by shutting off Wi-Fi, Bluetooth, HDMI, and status LEDs,
and lowering the CPU scaling governor between scheduled E-ink display refreshes.

Always starts in an INACTIVE state on boot / power-loss so the user can connect
over Wi-Fi to configure the device without being locked out.
"""

import glob
import logging
import os
import shutil
import socket
import subprocess
import time

logger = logging.getLogger(__name__)


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

    def __init__(self, device_config=None, dev_mode=False):
        self.device_config = device_config
        self.dev_mode = dev_mode
        self.is_pi = is_raspberry_pi() and not dev_mode

        # Runtime-only state: always False on boot / power-loss for safety
        self.is_active = False
        self.is_in_low_power = False
        self.keep_awake_until = 0.0

        logger.info(f"PowerManager initialized (is_raspberry_pi={self.is_pi}, dev_mode={self.dev_mode})")

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

    def set_led_power(self, enable: bool):
        """Toggle Raspberry Pi onboard activity LEDs."""
        if not self.is_pi:
            logger.info(f"[Mock] Status LEDs set to {enable}")
            return True
        brightness_val = "1" if enable else "0"
        for led_path in glob.glob("/sys/class/leds/*/brightness"):
            try:
                with open(led_path, "w") as f:
                    f.write(brightness_val)
            except Exception:
                pass
        return True

    def set_cpu_governor(self, governor="powersave"):
        """Set CPU scaling governor to save energy while idle."""
        if not self.is_pi:
            logger.info(f"[Mock] CPU governor set to {governor}")
            return True
        for gov_path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"):
            try:
                with open(gov_path, "w") as f:
                    f.write(governor)
            except Exception:
                pass
        return True

    # --- High-level Power Modes ---

    def enter_low_power_mode(self):
        """Enter deep idle power saving mode."""
        if self.is_in_low_power:
            return
        config = self.get_config()
        logger.info("Entering low-power idle mode...")

        if config.get("turn_off_led", True):
            self.set_led_power(False)

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

        config = self.get_config()
        if config.get("turn_off_wifi", True):
            self.enable_wifi()
            self.wait_for_network(timeout=25)

        self.set_cpu_governor("ondemand")

        if config.get("turn_off_bluetooth", True):
            self.enable_bluetooth()

        if config.get("turn_off_hdmi", True):
            self.set_hdmi_power(True)

        if config.get("turn_off_led", True):
            self.set_led_power(True)

        self.is_in_low_power = False
        logger.info("Peripherals restored to normal mode.")
