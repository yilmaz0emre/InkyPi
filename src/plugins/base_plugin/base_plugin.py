import logging
import os
from utils.app_utils import resolve_path, get_fonts
from utils.image_utils import take_screenshot_html
from utils.image_loader import AdaptiveImageLoader
from utils import i18n
from utils.i18n import N_
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from PIL import Image
import asyncio
import base64

logger = logging.getLogger(__name__)

# When set (see capture_rendered_html), render_image() records the rendered HTML instead of
# taking a browser screenshot, so a dashboard can compose several plugins into a single render.
_html_capture = ContextVar("html_capture", default=None)


@contextmanager
def capture_rendered_html():
    """Collect the HTML of every render_image() call made inside this block.

    Yields a list of {"html": str, "dimensions": (w, h)} entries. While active, render_image()
    returns a blank placeholder image instead of screenshotting.
    """
    captured = []
    token = _html_capture.set(captured)
    try:
        yield captured
    finally:
        _html_capture.reset(token)

STATIC_DIR = resolve_path("static")
PLUGINS_DIR = resolve_path("plugins")
BASE_PLUGIN_DIR =  os.path.join(PLUGINS_DIR, "base_plugin")
BASE_PLUGIN_RENDER_DIR = os.path.join(BASE_PLUGIN_DIR, "render")

FRAME_STYLES = [
    {
        "name": N_("None"),
        "icon": "frames/blank.png"
    },
    {
        "name": N_("Corner"),
        "icon": "frames/corner.png"
    },
    {
        "name": N_("Top and Bottom"),
        "icon": "frames/top_and_bottom.png"
    },
    {
        "name": N_("Rectangle"),
        "icon": "frames/rectangle.png"
    }
]

class BasePlugin:
    """Base class for all plugins."""
    def __init__(self, config, **dependencies):
        self.config = config

        # Initialize adaptive image loader for device-aware image processing
        self.image_loader = AdaptiveImageLoader()

        self.render_dir = self.get_plugin_dir("render")
        if os.path.exists(self.render_dir):
            # instantiate jinja2 env with base plugin and current plugin render directories
            loader = FileSystemLoader([self.render_dir, BASE_PLUGIN_RENDER_DIR])
            self.env = Environment(
                loader=loader,
                autoescape=select_autoescape(['html', 'xml']),
                extensions=['jinja2.ext.i18n']
            )
            # resolve the language on every call so changes in settings apply immediately
            self.env.install_gettext_callables(
                lambda message: i18n.get_translations(i18n.get_language()).gettext(message),
                lambda singular, plural, num: i18n.get_translations(i18n.get_language()).ngettext(singular, plural, num),
                newstyle=True
            )
            self.env.globals["format_date"] = i18n.format_date

    def generate_image(self, settings, device_config):
        raise NotImplementedError("generate_image must be implemented by subclasses")

    def cleanup(self, settings):
        """Optional cleanup method that plugins can override to delete associated resources.

        Called when a plugin instance is deleted. Plugins should override this to clean up
        any files, external resources, or other data associated with the plugin instance.

        Args:
            settings: The plugin instance's settings dict, which may contain file paths or other resources
        """
        pass  # Default implementation does nothing

    def get_plugin_id(self):
        return self.config.get("id")

    def get_plugin_dir(self, path=None):
        plugin_dir = os.path.join(PLUGINS_DIR, self.get_plugin_id())
        if path:
            plugin_dir = os.path.join(plugin_dir, path)
        return plugin_dir

    def generate_settings_template(self):
        template_params = {"settings_template": "base_plugin/settings.html"}

        settings_path = self.get_plugin_dir("settings.html")
        if Path(settings_path).is_file():
            template_params["settings_template"] = f"{self.get_plugin_id()}/settings.html"

        template_params['frame_styles'] = FRAME_STYLES
        return template_params

    def render_image(self, dimensions, html_file, css_file=None, template_params={}):
        rendered_html = self.render_html(dimensions, html_file, css_file, template_params)

        captured = _html_capture.get()
        if captured is not None:
            captured.append({"html": rendered_html, "dimensions": tuple(dimensions)})
            return Image.new("RGB", tuple(dimensions), "white")

        return take_screenshot_html(rendered_html, dimensions)

    def render_html(self, dimensions, html_file, css_file=None, template_params={}):
        """Render the plugin template to a standalone HTML document (no screenshot)."""
        # load the base plugin and current plugin css files
        css_files = [os.path.join(BASE_PLUGIN_RENDER_DIR, "plugin.css")]
        if css_file:
            plugin_css = os.path.join(self.render_dir, css_file)
            css_files.append(plugin_css)

        template_params["style_sheets"] = css_files
        template_params["width"] = dimensions[0]
        template_params["height"] = dimensions[1]
        template_params["font_faces"] = get_fonts()
        template_params["static_dir"] = STATIC_DIR
        template_params["language"] = i18n.get_language()

        # load and render the given html template
        template = self.env.get_template(html_file)
        return template.render(template_params)
