import json
import os
import shutil
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from config import Config
from model import Playlist, PluginInstance
from plugins.base_plugin.base_plugin import capture_rendered_html
from plugins.currency_gold import currency_gold as cg
from plugins.dashboard import dashboard as db
from plugins.plugin_registry import load_plugins
from utils import i18n


@pytest.fixture
def device_config(tmp_path, monkeypatch):
    """Real Config backed by a temporary copy of the dev config, with all plugins loaded."""
    config_file = tmp_path / "device.json"
    shutil.copy(os.path.join(Config.BASE_DIR, "config", "device_dev.json"), config_file)
    monkeypatch.setattr(Config, "config_file", str(config_file))
    config = Config()
    monkeypatch.setattr(config, "plugin_image_dir", str(tmp_path), raising=False)
    # start from one empty playlist, whatever the local dev config contains
    playlist_manager = config.get_playlist_manager()
    playlist_manager.playlists = []
    playlist_manager.add_default_playlist()
    load_plugins(config.get_plugins())
    i18n.init(config)
    yield config
    i18n.init(None)


def add_instance(config, plugin_id, name, settings=None, dashboard_only=True):
    playlist_manager = config.get_playlist_manager()
    if not playlist_manager.playlists:
        playlist_manager.add_default_playlist()
    playlist_manager.playlists[0].add_plugin({
        "plugin_id": plugin_id, "name": name, "plugin_settings": settings or {},
        "refresh": {"interval": 3600}, "dashboard_only": dashboard_only,
    })


FAKE_QUOTES = ({"USD": {"buying": 50.5, "selling": 50.6, "change": 0.2}}, None, "Test")


# --- capture mode ---

def test_capture_mode_returns_html_instead_of_screenshot(monkeypatch):
    screenshots = []
    monkeypatch.setattr("plugins.base_plugin.base_plugin.take_screenshot_html", lambda *a: screenshots.append(a))
    plugin = cg.CurrencyGold({"id": "currency_gold"})

    with capture_rendered_html() as captured:
        image = plugin.render_image((300, 200), "currency_gold.html", "currency_gold.css", {
            "title": "T", "groups": [], "show_change": True, "updated": "", "source": "",
            "font_scale": 1, "row_count": 1, "plugin_settings": {}})

    assert screenshots == []
    assert image.size == (300, 200)
    assert len(captured) == 1
    assert captured[0]["dimensions"] == (300, 200)
    assert "currency-gold" in captured[0]["html"]


def test_capture_mode_is_scoped(monkeypatch):
    monkeypatch.setattr("plugins.base_plugin.base_plugin.take_screenshot_html", lambda *a: "screenshot")
    plugin = cg.CurrencyGold({"id": "currency_gold"})
    with capture_rendered_html():
        pass

    assert plugin.render_image((10, 10), "currency_gold.html", None, {"groups": [], "plugin_settings": {}}) == "screenshot"


# --- layout ---

def test_parse_layout_sanitizes_input():
    layout = db.parse_layout({
        "layout": json.dumps({"widgets": [
            {"plugin_id": "clock", "name": "Saat", "x": 10, "y": -3, "w": 99, "h": 0},
            {"plugin_id": "weather"},            # no name -> dropped
            "garbage",                           # not a dict -> dropped
        ]}),
        "gap": "500",
        "showBorders": "false",
    })

    assert layout["columns"] == db.GRID_COLUMNS
    assert layout["widgets"] == [{"plugin_id": "clock", "name": "Saat", "x": 10, "y": 0, "w": 2, "h": 1}]
    assert layout["gap"] == db.MAX_GAP
    assert layout["borders"] is False


def test_parse_layout_invalid_json():
    assert db.parse_layout({"layout": "{not json"})["widgets"] == []
    assert db.parse_layout({})["gap"] == db.DEFAULT_GAP


def test_compute_rects_tiles_screen_with_gaps():
    layout = db.parse_layout({"layout": json.dumps({"widgets": [
        {"plugin_id": "a", "name": "A", "x": 0, "y": 0, "w": 6, "h": 12},
        {"plugin_id": "b", "name": "B", "x": 6, "y": 0, "w": 6, "h": 6},
        {"plugin_id": "c", "name": "C", "x": 6, "y": 6, "w": 6, "h": 6},
    ]}), "gap": "6"})

    a, b, c = db.compute_rects(layout, (800, 480))

    assert (a["left"], a["top"], a["width"], a["height"]) == (0, 0, 397, 480)
    assert (b["left"], b["top"], b["width"], b["height"]) == (403, 0, 397, 237)
    assert (c["left"], c["top"], c["width"], c["height"]) == (403, 243, 397, 237)


def test_widget_device_config_overrides_size_and_orientation():
    device_config = MagicMock()
    device_config.get_config.side_effect = lambda key=None, default=None: {"orientation": "vertical", "timezone": "Europe/Istanbul"}.get(key, default)

    widget_config = db.WidgetDeviceConfig(device_config, (320, 200))

    assert widget_config.get_resolution() == (320, 200)
    assert widget_config.get_config("orientation") == "horizontal"
    assert widget_config.get_config("timezone") == "Europe/Istanbul"
    widget_config.load_env_key("KEY")
    device_config.load_env_key.assert_called_once_with("KEY")


# --- widgets ---

def _rect(width=300, height=200, border=True):
    return {"left": 0, "top": 0, "width": width, "height": height, "border": border}


@pytest.mark.parametrize("size, label", [
    ((300, 200), ">$<"),                # small box -> currency plugin switches to short labels
    ((600, 400), "Amerikan Doları"),
])
def test_html_widget_is_captured_at_inner_size(device_config, monkeypatch, size, label):
    monkeypatch.setattr(cg, "fetch_quotes", lambda selected: FAKE_QUOTES)
    add_instance(device_config, "currency_gold", "Kurlar")

    result = db.render_widget({"plugin_id": "currency_gold", "name": "Kurlar"}, _rect(*size), device_config)

    assert result["kind"] == "html"
    assert (result["inner_width"], result["inner_height"]) == (size[0] - 2 * db.BORDER_WIDTH, size[1] - 2 * db.BORDER_WIDTH)
    assert label in result["html"]


def test_image_widget_is_embedded_and_resized(device_config, monkeypatch):
    add_instance(device_config, "clock", "Saat", {"selectedClockFace": "Digital Clock"})

    result = db.render_widget({"plugin_id": "clock", "name": "Saat"}, _rect(border=False), device_config)

    assert result["kind"] == "image"
    assert result["src"].startswith("data:image/png;base64,")
    assert (result["inner_width"], result["inner_height"]) == (300, 200)


@pytest.mark.parametrize("widget", [
    {"plugin_id": "clock", "name": "Does Not Exist"},
    {"plugin_id": "dashboard", "name": "Nested"},
])
def test_failing_widget_becomes_error_box(device_config, widget):
    result = db.render_widget(widget, _rect(), device_config)

    assert result["kind"] == "error"
    assert result["error"]


def test_dashboard_renders_with_single_screenshot(device_config, monkeypatch):
    monkeypatch.setattr(cg, "fetch_quotes", lambda selected: FAKE_QUOTES)
    add_instance(device_config, "currency_gold", "Kurlar")
    add_instance(device_config, "clock", "Saat", {"selectedClockFace": "Digital Clock"})

    pages = []

    def fake_screenshot(page, dimensions):
        # widget files must exist while the browser renders the page
        sources = [line.split('src="')[1].split('"')[0] for line in page.splitlines() if "<iframe" in line]
        pages.append({"page": page, "dimensions": dimensions, "sources": sources,
                      "exist": [os.path.exists(src.replace("file://", "")) for src in sources]})
        return Image.new("RGB", dimensions, "white")

    monkeypatch.setattr(db, "take_screenshot_html", fake_screenshot)
    plugin = db.Dashboard({"id": "dashboard"})
    layout = {"widgets": [
        {"plugin_id": "clock", "name": "Saat", "x": 0, "y": 0, "w": 6, "h": 12},
        {"plugin_id": "currency_gold", "name": "Kurlar", "x": 6, "y": 0, "w": 6, "h": 12},
        {"plugin_id": "weather", "name": "Missing", "x": 0, "y": 0, "w": 2, "h": 2},
    ]}

    image = plugin.generate_image({"layout": json.dumps(layout)}, device_config)

    assert image.size == (800, 480)
    assert len(pages) == 1
    page = pages[0]
    assert len(page["sources"]) == 1 and page["exist"] == [True]
    assert "data:image/png;base64," in page["page"]
    assert "Missing" in page["page"]
    assert not os.path.exists(page["sources"][0].replace("file://", ""))  # cleaned up


def test_dashboard_without_widgets_raises(device_config):
    with pytest.raises(RuntimeError):
        db.Dashboard({"id": "dashboard"}).generate_image({}, device_config)


def test_list_widget_instances_excludes_dashboards(device_config):
    add_instance(device_config, "clock", "Saat")
    add_instance(device_config, "dashboard", "Ana Pano", dashboard_only=False)

    instances = db.list_widget_instances(device_config)

    assert [(i["plugin_id"], i["name"], i["dashboard_only"]) for i in instances] == [("clock", "Saat", True)]
    assert instances[0]["plugin_name"] == "Saat"


# --- dashboard-only instances ---

def _instance(name, dashboard_only=False):
    return {"plugin_id": "clock", "name": name, "plugin_settings": {}, "refresh": {"interval": 60},
            "dashboard_only": dashboard_only}


def test_rotation_skips_dashboard_only_instances():
    playlist = Playlist("P", "00:00", "24:00", plugins=[
        _instance("A"), _instance("Widget", dashboard_only=True), _instance("B")])

    names = [playlist.get_next_plugin().name for _ in range(4)]

    assert names == ["A", "B", "A", "B"]
    assert playlist.has_rotating_plugins()


def test_rotation_with_only_dashboard_instances():
    playlist = Playlist("P", "00:00", "24:00", plugins=[_instance("Widget", dashboard_only=True)])

    assert playlist.get_next_plugin() is None
    assert not playlist.has_rotating_plugins()


def test_dashboard_only_round_trip_and_default():
    assert PluginInstance.from_dict(_instance("A", True)).to_dict()["dashboard_only"] is True
    legacy = {"plugin_id": "clock", "name": "Old", "plugin_settings": {}, "refresh": {}}
    assert PluginInstance.from_dict(legacy).dashboard_only is False


# --- routes ---

@pytest.fixture
def client(device_config):
    from blueprints.playlist import playlist_bp
    from blueprints.plugin import plugin_bp

    app = Flask(__name__)
    app.config.update(DEVICE_CONFIG=device_config, REFRESH_TASK=MagicMock(), DISPLAY_MANAGER=MagicMock())
    i18n.init_app(app, device_config)
    app.register_blueprint(plugin_bp)
    app.register_blueprint(playlist_bp)
    return app.test_client()


def test_add_plugin_saves_dashboard_only_flag(client, device_config):
    playlist = device_config.get_playlist_manager().playlists[0].name
    refresh = {"playlist": playlist, "instance_name": "Widget Saat", "refreshType": "interval",
               "unit": "hour", "interval": "1", "dashboardOnly": "on"}

    res = client.post("/add_plugin", data={"plugin_id": "clock", "refresh_settings": json.dumps(refresh)})

    assert res.status_code == 200, res.get_json()
    instance = device_config.get_playlist_manager().find_plugin("clock", "Widget Saat")
    assert instance.dashboard_only is True
    assert "dashboardOnly" not in instance.settings


def test_update_instance_toggles_dashboard_only(client, device_config):
    add_instance(device_config, "clock", "Saat", {"selectedClockFace": "Digital Clock"}, dashboard_only=False)

    res = client.put("/update_plugin_instance/Saat", data={
        "plugin_id": "clock", "dashboard_only": "true",
        "refresh_settings": json.dumps({"refreshType": "interval", "unit": "hour", "interval": "1"})})

    assert res.status_code == 200, res.get_json()
    instance = device_config.get_playlist_manager().find_plugin("clock", "Saat")
    assert instance.dashboard_only is True
    assert instance.settings == {"selectedClockFace": "Digital Clock"}


def test_preview_returns_png_without_updating_display(client, device_config):
    res = client.post("/preview_plugin", data={"plugin_id": "clock", "selectedClockFace": "Digital Clock"})

    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert Image.open(__import__("io").BytesIO(res.data)).size == (800, 480)
    client.application.config["DISPLAY_MANAGER"].display_image.assert_not_called()


def test_preview_unknown_plugin(client):
    res = client.post("/preview_plugin", data={"plugin_id": "nope"})

    assert res.status_code == 404


def test_list_widget_plugins_sorted_without_dashboard(device_config):
    plugins = db.list_widget_plugins(device_config)

    ids = [p["id"] for p in plugins]
    assert "dashboard" not in ids
    assert {"clock", "weather", "currency_gold", "calendar"} <= set(ids)
    names = [p["name"] for p in plugins]
    assert names == sorted(names, key=str.casefold)
    assert "Döviz ve Altın" in names  # translated


def test_dashboard_instances_endpoint(client, device_config):
    add_instance(device_config, "clock", "Saat")

    res = client.get("/dashboard_instances")

    assert res.status_code == 200
    assert [(i["plugin_id"], i["name"]) for i in res.get_json()] == [("clock", "Saat")]


def test_plugin_page_for_dashboard_prechecks_option(client, monkeypatch):
    import blueprints.plugin as plugin_module
    rendered = {}
    monkeypatch.setattr(plugin_module, "render_template", lambda template, **params: rendered.update(params) or "ok")

    client.get("/plugin/clock?for_dashboard=1")
    assert rendered["for_dashboard"] is True

    client.get("/plugin/clock")
    assert rendered["for_dashboard"] is False

    client.get("/plugin/dashboard?for_dashboard=1")
    assert rendered["for_dashboard"] is False
