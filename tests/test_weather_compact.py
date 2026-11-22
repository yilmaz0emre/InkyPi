from unittest.mock import MagicMock

import pytest

from plugins.weather.weather import COMPACT_FORECAST_MIN_HEIGHT, Weather
from utils import i18n
from utils.compact_layout import use_compact_layout

FORECAST = [
    {"day": "Paz", "high": 16, "low": 11, "icon": "today.png"},
    {"day": "Pzt", "high": 19, "low": 12, "icon": "mon.png"},
    {"day": "Sal", "high": 12, "low": 4, "icon": "tue.png"},
    {"day": "Çar", "high": 10, "low": 3, "icon": "wed.png"},
    {"day": "Per", "high": 11, "low": 5, "icon": "thu.png"},
]


@pytest.mark.parametrize("style, dimensions, expected", [
    ("auto", (800, 480), False),
    ("auto", (333, 240), True),
    ("short", (800, 480), True),
    ("full", (200, 150), False),
])
def test_shared_compact_rule(style, dimensions, expected):
    assert use_compact_layout(style, dimensions) is expected


@pytest.mark.parametrize("compact, settings, dimensions, expected_days", [
    (False, {"displayForecast": "true", "forecastDays": "5"}, (800, 480), []),           # full layout
    (True, {}, (333, 320), []),                                                         # forecast disabled
    (True, {"displayForecast": "true", "forecastDays": "5"}, (333, COMPACT_FORECAST_MIN_HEIGHT - 1), []),  # too short
    (True, {"displayForecast": "true", "forecastDays": "5"}, (333, 320), ["Pzt", "Sal", "Çar"]),  # max 3
    (True, {"displayForecast": "true", "forecastDays": "5"}, (260, 320), ["Pzt", "Sal"]),         # narrow: 2
    (True, {"displayForecast": "true", "forecastDays": "1"}, (333, 320), ["Pzt"]),
    (True, {"displayForecast": "true", "forecastDays": "x"}, (333, 320), ["Pzt", "Sal", "Çar"]),
])
def test_compact_forecast_days(compact, settings, dimensions, expected_days):
    params = {"compact": compact, "forecast": FORECAST}

    days = Weather.get_compact_forecast(params, settings, dimensions)

    assert [d["day"] for d in days] == expected_days


def _render(compact, compact_forecast=()):
    config = MagicMock()
    config.get_config.side_effect = lambda key=None, default=None: {"language": "tr"}.get(key, default)
    i18n.init(config)
    try:
        plugin = Weather({"id": "weather"})
        return plugin.env.get_template("weather.html").render(
            compact=compact, compact_forecast=list(compact_forecast), title="İstanbul", current_date="22 Kasım Paz",
            current_day_icon="now.png", current_temperature=16, temperature_unit="°C", feels_like=14,
            units="metric", forecast=FORECAST, data_points=[], hourly_forecast=[], plugin_settings={},
            last_refresh_time="", static_dir="")
    finally:
        i18n.init(None)


def test_compact_template_shows_only_essentials():
    html = _render(compact=True, compact_forecast=FORECAST[1:3])

    assert 'class="weather-compact with-forecast"' in html
    assert "İstanbul" in html and "16°" in html and "▲16° ▼11°" in html
    assert "Pzt" in html and "Sal" in html
    for hidden in ["22 Kasım Paz", "Hissedilen", "hourlyTemperatureChart", "chart.js", "weather-dashboard"]:
        assert hidden not in html, hidden


def test_full_template_unchanged_when_not_compact():
    html = _render(compact=False)

    assert "weather-compact" not in html
    assert "weather-dashboard" in html and "22 Kasım Paz" in html and "chart.js" in html
