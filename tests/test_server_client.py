import json
import os
from email.utils import formatdate
from io import BytesIO
from unittest.mock import MagicMock

import pytest
import requests
from flask import Flask
from PIL import Image

import thin_client as tc
from display.server_display import ServerDisplay


def png_bytes(color="red", size=(800, 480)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


# --- server side ---

def test_server_display_writes_single_panel_image(tmp_path):
    device_config = MagicMock()
    device_config.display_image_file = str(tmp_path / "images" / "display_image.png")
    display = ServerDisplay(device_config)

    for color in ["red", "blue"]:
        display.display_image(Image.new("RGB", (800, 480), color))

    assert os.listdir(tmp_path / "images") == ["display_image.png"]  # no history, no temp file
    with Image.open(device_config.display_image_file) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)


def test_display_manager_supports_server_type(tmp_path):
    from display.display_manager import DisplayManager

    device_config = MagicMock()
    # like Config.get_config: missing keys return the default ({})
    device_config.get_config.side_effect = lambda key=None, default={}: {
        "display_type": "server", "orientation": "vertical"}.get(key, default)
    device_config.get_resolution.return_value = (800, 480)
    device_config.current_image_file = str(tmp_path / "current_image.png")
    device_config.display_image_file = str(tmp_path / "display_image.png")

    DisplayManager(device_config).display_image(Image.new("RGB", (800, 480), "white"))

    # the served image is already processed for the panel (rotated for vertical orientation)
    with Image.open(device_config.display_image_file) as image:
        assert image.size == (800, 480)
    assert os.path.exists(device_config.current_image_file)


@pytest.fixture
def server(tmp_path):
    from blueprints.main import main_bp
    from utils import i18n

    device_config = MagicMock()
    device_config.get_config.side_effect = lambda key=None, default={}: {"language": "en"}.get(key, default)
    device_config.display_image_file = str(tmp_path / "display_image.png")
    app = Flask(__name__)
    app.config["DEVICE_CONFIG"] = device_config
    i18n.init_app(app, device_config)
    app.register_blueprint(main_bp)
    return app.test_client(), device_config


def test_display_image_endpoint_conditional_requests(server):
    client, device_config = server
    assert client.get("/api/display_image").status_code == 404

    with open(device_config.display_image_file, "wb") as f:
        f.write(png_bytes())
    mtime = 1_800_000_000
    os.utime(device_config.display_image_file, (mtime, mtime))

    first = client.get("/api/display_image")
    assert first.status_code == 200
    assert first.mimetype == "image/png"
    assert first.headers["Last-Modified"] == formatdate(mtime, usegmt=True)  # real GMT

    again = client.get("/api/display_image", headers={"If-Modified-Since": first.headers["Last-Modified"]})
    assert again.status_code == 304

    os.utime(device_config.display_image_file, (mtime + 60, mtime + 60))
    changed = client.get("/api/display_image", headers={"If-Modified-Since": first.headers["Last-Modified"]})
    assert changed.status_code == 200


# --- thin client ---

class FakeResponse:
    def __init__(self, status, content=b"", last_modified=None):
        self.status_code = status
        self.content = content
        self.headers = {"Last-Modified": last_modified} if last_modified else {}


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def make_client(tmp_path):
    def make(responses, **kwargs):
        session = MagicMock()
        session.get.side_effect = responses
        display = MagicMock()
        clock = FakeClock()
        client = tc.ThinClient(
            server_url="http://pi5.local", display=display, resolution=(800, 480),
            state_dir=str(tmp_path / "state"), session=session, sleep=clock.sleep, now=clock.now,
            offline_label=lambda ts: "Offline", **kwargs)
        return client, display, session, clock
    return make


def test_new_image_is_displayed_and_remembered(make_client, tmp_path):
    client, display, session, _ = make_client([FakeResponse(200, png_bytes(), "Sun, 22 Nov 2026 12:00:00 GMT")])

    assert client.step() == "new"

    display.display_image.assert_called_once()
    assert display.display_image.call_args[0][0].size == (800, 480)
    session.get.assert_called_once_with("http://pi5.local/api/display_image", headers={}, timeout=tc.REQUEST_TIMEOUT)
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    assert state["last_modified"] == "Sun, 22 Nov 2026 12:00:00 GMT"


def test_unchanged_image_never_refreshes_panel(make_client):
    lm = "Sun, 22 Nov 2026 12:00:00 GMT"
    client, display, session, _ = make_client([FakeResponse(200, png_bytes(), lm), FakeResponse(304), FakeResponse(304)])

    assert [client.step() for _ in range(3)] == ["new", "unchanged", "unchanged"]

    assert display.display_image.call_count == 1
    assert session.get.call_args.kwargs["headers"] == {"If-Modified-Since": lm}


def test_state_survives_restart(make_client):
    lm = "Sun, 22 Nov 2026 12:00:00 GMT"
    client, *_ = make_client([FakeResponse(200, png_bytes(), lm)])
    client.step()

    restarted, display, session, _ = make_client([FakeResponse(304)])
    assert restarted.step() == "unchanged"
    assert session.get.call_args.kwargs["headers"] == {"If-Modified-Since": lm}
    display.display_image.assert_not_called()  # no refresh after reboot


def test_wrong_size_is_resized(make_client):
    client, display, *_ = make_client([FakeResponse(200, png_bytes(size=(400, 240)), "x")])

    client.step()

    assert display.display_image.call_args[0][0].size == (800, 480)


def test_offline_badge_once_then_cleared(make_client):
    error = requests.ConnectionError("down")
    responses = [FakeResponse(200, png_bytes("white"), "x"), error, error, error, FakeResponse(304)]
    client, display, _, clock = make_client(responses, offline_after=600)

    client.step()                      # image shown
    clock.sleep(300); client.step()    # 5 min offline: nothing yet
    assert display.display_image.call_count == 1

    clock.sleep(400); client.step()    # > 10 min: badge drawn once
    assert display.display_image.call_count == 2
    badged = display.display_image.call_args[0][0]
    from PIL import ImageChops
    changed = ImageChops.difference(badged, Image.new("RGB", (800, 480), "white")).getbbox()
    assert changed is not None and changed[0] > 400 and changed[1] > 400  # small badge, bottom right

    clock.sleep(300); client.step()    # still offline: no extra refresh
    assert display.display_image.call_count == 2

    assert client.step() == "unchanged"  # back online, same image: badge removed
    assert display.display_image.call_count == 3
    assert client.state["offline_shown"] is False


def test_empty_server_and_http_errors(make_client):
    client, display, *_ = make_client([FakeResponse(404), FakeResponse(500), FakeResponse(200, b"not a png", "x")])

    assert client.step() == "empty"
    assert client.step() == "error"
    assert client.step() == "error"
    display.display_image.assert_not_called()


def test_power_save_after_boot_window(make_client):
    power = MagicMock()
    client, _, _, clock = make_client([], power_manager=power, power_save=True, boot_awake=600, poll_interval=300)

    client.wait_for_next_poll()   # t=0: within the boot window, stay awake (reachable for SSH)
    power.enter_low_power_mode.assert_not_called()

    clock.sleep(600)
    client.wait_for_next_poll()   # after the window: Wi-Fi etc. off while sleeping
    power.enter_low_power_mode.assert_called_once()
    power.exit_low_power_mode.assert_called_once()


def test_power_save_disabled_only_sleeps(make_client):
    power = MagicMock()
    client, _, _, clock = make_client([], power_manager=power, power_save=False, boot_awake=0, poll_interval=120)
    start = clock.now()

    client.wait_for_next_poll()

    power.enter_low_power_mode.assert_not_called()
    assert clock.now() - start == 120


def test_load_settings():
    device_config = MagicMock()
    device_config.get_config.return_value = {"server_url": "http://pi5.local/", "poll_interval": 5, "power_save": 1}

    settings = tc.load_settings(device_config)

    assert settings["server_url"] == "http://pi5.local"
    assert settings["poll_interval"] == tc.MIN_POLL_INTERVAL
    assert settings["power_save"] is True
    assert settings["offline_after"] == tc.DEFAULTS["offline_after"]

    device_config.get_config.return_value = {}
    with pytest.raises(ValueError):
        tc.load_settings(device_config)
