"""Thin clients seen by a render server (display_type "server").

Each poll of /api/display_image carries the client's name and current settings in request headers.
The server remembers the last report per client (for the Settings page) and answers with the
settings chosen in its web UI, which the client applies and keeps.
"""
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CLIENT_HEADER = "X-InkyPi-Client"
SETTINGS_HEADER = "X-InkyPi-Client-Settings"

# limits shared by the server UI and the client
MIN_POLL_INTERVAL = 30
MAX_POLL_INTERVAL = 24 * 3600
MAX_OFFLINE_AFTER = 7 * 24 * 3600

_lock = threading.Lock()


def sanitize_settings(settings):
    """Keep only known, valid keys; returns {} for anything unusable."""
    if not isinstance(settings, dict):
        return {}
    clean = {}
    if "power_save" in settings:
        clean["power_save"] = bool(settings["power_save"])
    try:
        if "poll_interval" in settings:
            clean["poll_interval"] = min(MAX_POLL_INTERVAL, max(MIN_POLL_INTERVAL, int(settings["poll_interval"])))
        if "offline_after" in settings:
            clean["offline_after"] = min(MAX_OFFLINE_AFTER, max(0, int(settings["offline_after"])))
    except (TypeError, ValueError):
        return {}
    return clean


def parse_settings_header(value):
    try:
        return sanitize_settings(json.loads(value)) if value else {}
    except ValueError:
        return {}


def state_file(device_config):
    return os.path.join(os.path.dirname(device_config.config_file), ".thin_clients.json")


def load_clients(device_config):
    try:
        with open(state_file(device_config)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_poll(device_config, name, reported, now=None):
    """Remember that `name` polled just now with the `reported` settings."""
    name = (name or "").strip()[:64]
    if not name:
        return
    with _lock:
        clients = load_clients(device_config)
        clients[name] = {"last_seen": int(now if now is not None else time.time()), **sanitize_settings(reported)}
        path = state_file(device_config)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(clients, f)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning(f"Could not store thin client state: {e}")


def desired_settings(device_config):
    """Settings chosen in the server's web UI for its thin clients ({} = leave them as they are)."""
    return sanitize_settings(device_config.get_config("thin_client_settings", default={}))


def client_overview(device_config, now=None):
    """Clients for the Settings page, most recently seen first, with pending/next-check info."""
    now = now if now is not None else time.time()
    wanted = desired_settings(device_config)
    overview = []
    for name, info in load_clients(device_config).items():
        last_seen = info.get("last_seen", 0)
        poll = info.get("poll_interval")
        overview.append({
            "name": name,
            "last_seen": last_seen,
            "seconds_ago": max(0, int(now - last_seen)),
            "next_check_in": max(0, int(last_seen + poll - now)) if poll else None,
            "power_save": info.get("power_save"),
            "poll_interval": poll,
            "offline_after": info.get("offline_after"),
            # the client reports what it runs with; differences are applied on its next poll
            "pending": any(info.get(key) != value for key, value in wanted.items()),
        })
    return sorted(overview, key=lambda c: c["last_seen"], reverse=True)
