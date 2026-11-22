"""Dashboard plugin: several saved plugin instances laid out on one screen.

Widgets reference existing plugin instances (configured on their own plugin pages and saved to a
playlist, optionally as "dashboard only"). Each widget is rendered at the size of its box, and the
whole dashboard is produced with a single browser screenshot:

* HTML-based plugins are rendered with render_image() in capture mode, which returns their HTML
  instead of screenshotting; each one is loaded in its own <iframe> so their CSS stays isolated
  and viewport units (vw/dvh) refer to the widget box.
* Plugins that draw with PIL (clock, images, ...) return an image, embedded as a data URI.
"""
import base64
import json
import logging
import tempfile
from io import BytesIO
from pathlib import Path

from plugins.base_plugin.base_plugin import BasePlugin, capture_rendered_html
from plugins.plugin_registry import get_plugin_instance
from utils import i18n
from utils.image_utils import take_screenshot_html

logger = logging.getLogger(__name__)

GRID_COLUMNS = 12
GRID_ROWS = 12
DEFAULT_GAP = 6
MAX_GAP = 40
BORDER_WIDTH = 2
DASHBOARD_PLUGIN_ID = "dashboard"


class WidgetDeviceConfig:
    """Device config seen by a widget: the widget box is its whole (horizontal) screen."""

    def __init__(self, device_config, dimensions):
        self._device_config = device_config
        self._dimensions = (int(dimensions[0]), int(dimensions[1]))

    def get_resolution(self):
        return self._dimensions

    def get_config(self, key=None, default={}):
        if key == "orientation":
            # dimensions are already oriented by the dashboard; don't let widgets swap them again
            return "horizontal"
        if key == "resolution":
            return list(self._dimensions)
        return self._device_config.get_config(key, default=default)

    def __getattr__(self, name):
        return getattr(self._device_config, name)


class Dashboard(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["dashboard_grid"] = {"columns": GRID_COLUMNS, "rows": GRID_ROWS}
        template_params["dashboard_instances"] = []
        template_params["dashboard_resolution"] = [800, 480]
        try:
            from flask import current_app
            device_config = current_app.config["DEVICE_CONFIG"]
            template_params["dashboard_instances"] = list_widget_instances(device_config)
            template_params["dashboard_resolution"] = list(get_oriented_resolution(device_config))
        except Exception as e:
            logger.warning(f"Could not load dashboard widget instances: {e}")
        return template_params

    def generate_image(self, settings, device_config):
        layout = parse_layout(settings)
        if not layout["widgets"]:
            raise RuntimeError(i18n.gettext("Add at least one widget to the dashboard."))

        dimensions = get_oriented_resolution(device_config)
        widgets = [
            render_widget(widget, rect, device_config)
            for widget, rect in zip(layout["widgets"], compute_rects(layout, dimensions))
        ]

        # Widget HTML goes into files loaded with <iframe src>, not <iframe srcdoc>: srcdoc documents
        # always use standards mode, while plugin templates (no doctype) are written for quirks mode,
        # e.g. the weather layout relies on <body> filling the viewport.
        with tempfile.TemporaryDirectory(prefix="inkypi_dashboard_") as tmp_dir:
            for index, widget in enumerate(widgets):
                if widget["kind"] == "html":
                    path = Path(tmp_dir) / f"widget_{index}.html"
                    path.write_text(widget.pop("html"), encoding="utf-8")
                    widget["src"] = path.as_uri()

            page = self.env.get_template("dashboard.html").render(
                width=dimensions[0], height=dimensions[1], widgets=widgets, border_width=BORDER_WIDTH)
            image = take_screenshot_html(page, dimensions)
        if image is None:
            raise RuntimeError(i18n.gettext("Failed to render the dashboard."))
        return image


def get_oriented_resolution(device_config):
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]
    return tuple(dimensions)


def list_widget_instances(device_config):
    """All saved plugin instances that can be placed on a dashboard."""
    plugin_names = {p.get("id"): p.get("display_name", p.get("id")) for p in device_config.get_plugins()}
    instances = []
    for playlist in device_config.get_playlist_manager().playlists:
        for instance in playlist.plugins:
            if instance.plugin_id == DASHBOARD_PLUGIN_ID:
                continue
            instances.append({
                "plugin_id": instance.plugin_id,
                "name": instance.name,
                "plugin_name": i18n.gettext(plugin_names.get(instance.plugin_id, instance.plugin_id)),
                "playlist": playlist.name,
                "dashboard_only": instance.dashboard_only,
            })
    return instances


def parse_layout(settings):
    """Read and sanitize the layout JSON saved by the editor."""
    raw = settings.get("layout") or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid dashboard layout JSON, ignoring it")
        data = {}

    columns = _clamp_int(data.get("columns"), 1, 48, GRID_COLUMNS)
    rows = _clamp_int(data.get("rows"), 1, 48, GRID_ROWS)
    widgets = []
    for widget in data.get("widgets") or []:
        if not isinstance(widget, dict) or not widget.get("plugin_id") or not widget.get("name"):
            continue
        x = _clamp_int(widget.get("x"), 0, columns - 1, 0)
        y = _clamp_int(widget.get("y"), 0, rows - 1, 0)
        widgets.append({
            "plugin_id": str(widget["plugin_id"]),
            "name": str(widget["name"]),
            "x": x,
            "y": y,
            "w": _clamp_int(widget.get("w"), 1, columns - x, columns - x),
            "h": _clamp_int(widget.get("h"), 1, rows - y, rows - y),
        })

    return {
        "columns": columns,
        "rows": rows,
        "widgets": widgets,
        "gap": _clamp_int(settings.get("gap"), 0, MAX_GAP, DEFAULT_GAP),
        "borders": settings.get("showBorders", "true") != "false",
    }


def compute_rects(layout, dimensions):
    """Pixel box (left, top, width, height) of each widget; grid lines are shared so boxes tile exactly."""
    width, height = dimensions
    columns, rows, gap = layout["columns"], layout["rows"], layout["gap"]
    half_gap = gap / 2

    def edge(index, cells, size):
        return round(index * size / cells)

    rects = []
    for widget in layout["widgets"]:
        left = edge(widget["x"], columns, width)
        right = edge(widget["x"] + widget["w"], columns, width)
        top = edge(widget["y"], rows, height)
        bottom = edge(widget["y"] + widget["h"], rows, height)
        # Gaps only between widgets and the screen edge, half on each side
        left += round(half_gap) if left > 0 else 0
        top += round(half_gap) if top > 0 else 0
        right -= round(half_gap) if right < width else 0
        bottom -= round(half_gap) if bottom < height else 0
        rects.append({
            "left": left,
            "top": top,
            "width": max(1, right - left),
            "height": max(1, bottom - top),
            "border": layout["borders"],
        })
    return rects


def render_widget(widget, rect, device_config):
    """Render one widget for its box; returns the data used by dashboard.html."""
    border = BORDER_WIDTH if rect["border"] else 0
    inner = (max(1, rect["width"] - 2 * border), max(1, rect["height"] - 2 * border))
    result = dict(rect, inner_width=inner[0], inner_height=inner[1], name=widget["name"])

    try:
        if widget["plugin_id"] == DASHBOARD_PLUGIN_ID:
            raise RuntimeError(i18n.gettext("A dashboard cannot contain another dashboard."))

        instance = device_config.get_playlist_manager().find_plugin(widget["plugin_id"], widget["name"])
        if not instance:
            raise RuntimeError(i18n.gettext("Plugin instance '%(name)s' not found.", name=widget["name"]))
        plugin_config = device_config.get_plugin(widget["plugin_id"])
        if not plugin_config:
            raise RuntimeError(i18n.gettext("Plugin '%(plugin_id)s' is not installed.", plugin_id=widget["plugin_id"]))

        plugin = get_plugin_instance(plugin_config)
        with capture_rendered_html() as captured:
            image = plugin.generate_image(instance.settings, WidgetDeviceConfig(device_config, inner))

        if captured:
            result.update(kind="html", html=captured[-1]["html"])
        elif image is not None:
            result.update(kind="image", src=_image_data_uri(image, inner))
        else:
            raise RuntimeError(i18n.gettext("The plugin returned no image."))
    except Exception as e:
        logger.exception(f"Dashboard widget '{widget['name']}' failed")
        result.update(kind="error", error=str(e))
    return result


def _image_data_uri(image, size):
    if image.size != tuple(size):
        image = image.resize(tuple(size))
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
