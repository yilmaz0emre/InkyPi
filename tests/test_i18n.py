from datetime import datetime
import pytest
from unittest.mock import MagicMock
from utils import i18n


def test_normalize_language():
    assert i18n.normalize_language("en") == "en"
    assert i18n.normalize_language("tr") == "tr"
    assert i18n.normalize_language("invalid") == "en"
    assert i18n.normalize_language(None) == "en"


def test_get_language_with_mock_config():
    config = MagicMock()
    config.get_config.return_value = "tr"
    assert i18n.get_language(config) == "tr"

    config.get_config.return_value = "en"
    assert i18n.get_language(config) == "en"

    config.get_config.return_value = "unknown"
    assert i18n.get_language(config) == "en"


def test_gettext_english_and_turkish():
    # In English (default fallback)
    assert i18n.gettext("Settings", language="en") == "Settings"
    assert i18n.gettext("Weather", language="en") == "Weather"

    # In Turkish
    assert i18n.gettext("Settings", language="tr") == "Ayarlar"
    assert i18n.gettext("Weather", language="tr") == "Hava Durumu"
    assert i18n.gettext("Playlists", language="tr") == "Oynatma Listeleri"
    assert i18n.gettext("Clock", language="tr") == "Saat"
    assert i18n.gettext("Countdown", language="tr") == "Geri Sayım"


def test_gettext_with_variables():
    msg = i18n.gettext("Last refresh: %(time)s", language="tr", time="14:30")
    assert msg == "Son yenileme: 14:30"

    msg_en = i18n.gettext("Last refresh: %(time)s", language="en", time="14:30")
    assert msg_en == "Last refresh: 14:30"


def test_format_date_localization():
    # Saturday, November 14, 2026
    dt = datetime(2026, 11, 14, 15, 30)

    tr_weekday = i18n.format_date(dt, skeleton="EEE", language="tr")
    assert "Cmt" in tr_weekday or "Cumartesi" in tr_weekday

    tr_full = i18n.format_date(dt, skeleton="MMMMEEEEd", language="tr")
    assert "Kasım" in tr_full

    en_weekday = i18n.format_date(dt, skeleton="EEE", language="en")
    assert "Sat" in en_weekday


def test_format_time():
    dt = datetime(2026, 11, 14, 15, 5)
    assert i18n.format_time(dt, "24h", language="tr") == "15:05"
    assert i18n.format_time(dt, "24h", language="en") == "15:05"


def test_plugin_render_template_translates_dynamic_titles():
    """Plugin templates translate N_()-marked values passed from Python, e.g. GitHub metric titles."""
    from plugins.base_plugin.base_plugin import BasePlugin

    config = MagicMock()
    config.get_config.side_effect = lambda key=None, default=None: {"language": "tr"}.get(key, default)
    i18n.init(config)
    try:
        html = BasePlugin({"id": "github"}).env.get_template("github_contributions.html").render(
            metrics=[{"title": "Current Streak", "value": 2}], username="x", grid=[], plugin_settings={})
    finally:
        i18n.init(None)

    assert "Güncel Seri" in html
