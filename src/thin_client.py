#!/usr/bin/env python3
"""Thin client: show the panel image rendered by an InkyPi server (e.g. a Raspberry Pi 5).

The server (display_type "server") does all the work: web UI, data fetching and the Chromium
render. This client only downloads the finished, panel-ready image from /api/display_image and
pushes it to the local e-paper panel, so a battery-powered Pi Zero needs no browser.

* The image is requested with If-Modified-Since; an unchanged image (304) never refreshes the
  panel, which matters for slow (~30 s) and power-hungry e-ink refreshes.
* If the server cannot be reached the panel keeps the last image. After `offline_after` seconds
  a small "offline" badge is drawn once, so stale data is recognisable.
* With `power_save` enabled the client turns Wi-Fi, LEDs, ... off between polls (PowerManager),
  but only after `boot_awake` seconds, so the device stays reachable right after booting.

Settings live in device.json under "thin_client", e.g.
    {"server_url": "http://pi5.local", "poll_interval": 300, "offline_after": 3600,
     "power_save": true, "boot_awake": 600}
"""
import json
import logging
import os
import time
from datetime import datetime
from io import BytesIO

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

DEFAULTS = {
    "server_url": "",
    "poll_interval": 300,
    "offline_after": 3600,
    "power_save": False,
    "boot_awake": 600,
}
MIN_POLL_INTERVAL = 30
REQUEST_TIMEOUT = 20


def load_settings(device_config):
    settings = dict(DEFAULTS)
    settings.update(device_config.get_config("thin_client", default={}) or {})
    if not settings["server_url"]:
        raise ValueError('thin_client.server_url is not set in device.json (e.g. "http://pi5.local")')
    settings["server_url"] = settings["server_url"].rstrip("/")
    settings["poll_interval"] = max(MIN_POLL_INTERVAL, int(settings["poll_interval"]))
    settings["offline_after"] = max(0, int(settings["offline_after"]))
    settings["boot_awake"] = max(0, int(settings["boot_awake"]))
    settings["power_save"] = bool(settings["power_save"])
    return settings


class ThinClient:
    def __init__(self, server_url, display, resolution, state_dir, power_manager=None,
                 poll_interval=300, offline_after=3600, power_save=False, boot_awake=600,
                 offline_label=None, session=None, sleep=time.sleep, now=time.time):
        self.image_url = f"{server_url}/api/display_image"
        self.display = display
        self.resolution = tuple(resolution)
        self.state_dir = state_dir
        self.power_manager = power_manager
        self.poll_interval = poll_interval
        self.offline_after = offline_after
        self.power_save = power_save
        self.boot_awake = boot_awake
        # callable(last_success_timestamp) -> badge text
        self.offline_label = offline_label or (lambda ts: "Offline")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.now = now

        self.started_at = now()
        os.makedirs(state_dir, exist_ok=True)
        self.state = self._load_state()
        # last contact with the server; start the offline clock at boot
        self.last_success = self.started_at

    # --- state (survives reboots: no panel refresh if the server image did not change) ---

    @property
    def _state_file(self):
        return os.path.join(self.state_dir, "state.json")

    @property
    def _image_file(self):
        return os.path.join(self.state_dir, "last_image.png")

    def _load_state(self):
        try:
            with open(self._state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        tmp = self._state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self._state_file)

    # --- one poll ---

    def fetch(self):
        """Returns ("new", image, last_modified), ("unchanged",), ("empty",) or ("error", message)."""
        headers = {}
        if self.state.get("last_modified"):
            headers["If-Modified-Since"] = self.state["last_modified"]
        try:
            response = self.session.get(self.image_url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            return ("error", str(e))

        if response.status_code == 304:
            return ("unchanged",)
        if response.status_code == 404:
            return ("empty",)  # server is up but has not rendered anything yet
        if response.status_code != 200:
            return ("error", f"HTTP {response.status_code}")
        try:
            image = Image.open(BytesIO(response.content))
            image.load()
        except Exception as e:
            return ("error", f"invalid image: {e}")
        return ("new", image, response.headers.get("Last-Modified"))

    def step(self):
        """Poll the server once and update the panel if needed. Returns the fetch outcome."""
        result = self.fetch()
        outcome = result[0]

        if outcome == "new":
            image = result[1]
            if image.size != self.resolution:
                logger.warning(f"Server image is {image.size}, panel is {self.resolution}; resizing. "
                               "Set the server resolution to match the panel.")
                image = image.resize(self.resolution)
            image = image.convert("RGB")
            logger.info("New image from server, updating panel")
            self.display.display_image(image)
            image.save(self._image_file, "PNG")
            self.state.update(last_modified=result[2], offline_shown=False)
            self._save_state()
            self.last_success = self.now()
        elif outcome in ("unchanged", "empty"):
            if outcome == "empty":
                logger.info("Server has no image yet")
            self.last_success = self.now()
            if self.state.get("offline_shown"):
                # back online but nothing new: remove the offline badge
                self._redisplay_last_image()
        else:
            logger.warning(f"Server unreachable: {result[1]}")
            if self.offline_after and self.now() - self.last_success >= self.offline_after \
                    and not self.state.get("offline_shown"):
                self._show_offline_badge()
        return outcome

    def _redisplay_last_image(self):
        if os.path.exists(self._image_file):
            with Image.open(self._image_file) as image:
                self.display.display_image(image.convert("RGB"))
        self.state["offline_shown"] = False
        self._save_state()

    def _show_offline_badge(self):
        """Draw a small badge on the last image once, so stale data is recognisable."""
        if not os.path.exists(self._image_file):
            return
        with Image.open(self._image_file) as last:
            image = last.convert("RGB")
        draw_offline_badge(image, self.offline_label(self.last_success))
        logger.info("Showing offline badge")
        self.display.display_image(image)
        self.state["offline_shown"] = True
        self._save_state()

    # --- main loop ---

    def wait_for_next_poll(self):
        in_boot_window = self.now() - self.started_at < self.boot_awake
        if self.power_save and self.power_manager and not in_boot_window:
            self.power_manager.enter_low_power_mode()
            try:
                self.sleep(self.poll_interval)
            finally:
                self.power_manager.exit_low_power_mode()
        else:
            self.sleep(self.poll_interval)

    def run(self):
        logger.info(f"Thin client polling {self.image_url} every {self.poll_interval}s "
                    f"(power save: {self.power_save})")
        while True:
            try:
                self.step()
            except Exception:
                logger.exception("Thin client poll failed")
            self.wait_for_next_poll()


def draw_offline_badge(image, text):
    from utils.app_utils import get_font

    draw = ImageDraw.Draw(image)
    size = max(12, image.height // 26)
    font = get_font("Jost", size)
    padding = size // 2
    bbox = draw.textbbox((0, 0), text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    right, bottom = image.width - padding, image.height - padding
    box = (right - width - 2 * padding, bottom - height - 2 * padding, right, bottom)
    draw.rectangle(box, fill="white", outline="black", width=2)
    draw.text((box[0] + padding - bbox[0], box[1] + padding - bbox[1]), text, fill="black", font=font)
    return image


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from config import Config
    from display.display_manager import DisplayManager
    from utils import i18n
    from utils.power_manager import PowerManager

    device_config = Config()
    i18n.init(device_config)
    settings = load_settings(device_config)
    time_format = device_config.get_config("time_format", default="24h")

    def offline_label(last_success):
        when = i18n.format_time(datetime.fromtimestamp(last_success), time_format)
        return i18n.gettext("Offline · last update %(time)s", time=when)

    display = DisplayManager(device_config).display
    client = ThinClient(
        server_url=settings["server_url"],
        display=display,
        resolution=device_config.get_resolution(),
        state_dir=os.path.join(Config.BASE_DIR, "config", ".thin_client"),
        power_manager=PowerManager(device_config),
        poll_interval=settings["poll_interval"],
        offline_after=settings["offline_after"],
        power_save=settings["power_save"],
        boot_awake=settings["boot_awake"],
        offline_label=offline_label,
    )
    client.run()


if __name__ == "__main__":
    main()
