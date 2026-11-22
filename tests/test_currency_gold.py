from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz
import requests

from plugins.currency_gold import currency_gold as cg
from utils import i18n

TRUNCGIL_SAMPLE = {
    "Update_Date": "2026-11-22 14:41:02",
    "USD": {"Buying": 50.5643, "Type": "Currency", "Selling": 50.5752, "Change": 0.22},
    "EUR": {"Buying": 55.4175, "Type": "Currency", "Selling": 55.6072, "Change": -0.03},
    "GBP": {"Buying": 66.1029, "Type": "Currency", "Selling": 66.3417, "Change": 0},
    "GRA": {"Buying": 5730.97, "Type": "Gold", "Selling": 5731.95, "Change": -3.48, "Name": "GRAMALTIN"},
    "CEYREKALTIN": {"Buying": 9698, "Type": "Gold", "Selling": 9912, "Change": -0.64, "Name": "CEYREKALTIN"},
    "ONS": {"Buying": 0, "Type": "Gold", "Selling": 0, "Change": -3.68, "Name": "ONS"},
}

TCMB_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Tarih_Date Tarih="20.11.2026" Date="11/20/2026">
  <Currency CrossOrder="0" Kod="USD" CurrencyCode="USD">
    <Unit>1</Unit><ForexBuying>50.4027</ForexBuying><ForexSelling>50.4936</ForexSelling>
  </Currency>
  <Currency CrossOrder="1" Kod="JPY" CurrencyCode="JPY">
    <Unit>100</Unit><ForexBuying>31.80</ForexBuying><ForexSelling>32.01</ForexSelling>
  </Currency>
  <Currency CrossOrder="9" Kod="EUR" CurrencyCode="EUR">
    <Unit>1</Unit><ForexBuying>55.30</ForexBuying><ForexSelling>55.40</ForexSelling>
  </Currency>
</Tarih_Date>"""


def _response(json_data=None, content=None, status=200):
    response = MagicMock()
    response.json.return_value = json_data
    response.content = content
    response.raise_for_status.side_effect = None if status == 200 else requests.HTTPError(str(status))
    return response


@pytest.fixture
def http(monkeypatch):
    """Route session.get by URL; tests set responses (or exceptions) in the returned dict."""
    routes = {}

    def fake_get(url, timeout=None):
        result = routes[url]
        if isinstance(result, Exception):
            raise result
        return result

    session = MagicMock()
    session.get.side_effect = fake_get
    monkeypatch.setattr(cg, "get_http_session", lambda: session)
    return routes


# --- settings ---

def test_selected_items_default_and_order():
    assert cg.get_selected_items({}) == cg.DEFAULT_ITEMS
    # Display order follows ITEMS, unknown keys are dropped
    assert cg.get_selected_items({"items[]": ["CEYREKALTIN", "BOGUS", "USD"]}) == ["USD", "CEYREKALTIN"]
    assert cg.get_selected_items({"items[]": "GRA"}) == ["GRA"]
    assert cg.get_selected_items({"items[]": ["BOGUS"]}) == cg.DEFAULT_ITEMS


# --- fetching ---

def test_truncgil_parsing(http):
    http[cg.TRUNCGIL_URL] = _response(TRUNCGIL_SAMPLE)

    quotes, updated_at, source = cg.fetch_quotes(["USD", "GRA", "CEYREKALTIN"])

    assert source == "Truncgil Finans"
    assert quotes["USD"] == {"buying": 50.5643, "selling": 50.5752, "change": 0.22}
    assert quotes["CEYREKALTIN"]["selling"] == 9912
    assert updated_at == pytz.timezone("Europe/Istanbul").localize(datetime(2026, 11, 22, 14, 41, 2))


def test_truncgil_zero_prices_are_skipped(http):
    http[cg.TRUNCGIL_URL] = _response(TRUNCGIL_SAMPLE)

    quotes, _, _ = cg.fetch_truncgil(["USD", "ONS"])

    assert "ONS" not in quotes


def test_fallback_to_tcmb_and_calculated_gram_gold(http):
    http[cg.TRUNCGIL_URL] = requests.ConnectionError("down")
    http[cg.TCMB_URL] = _response(content=TCMB_SAMPLE)
    http[cg.GOLD_SPOT_URL] = _response({"price": 3539.0})

    quotes, updated_at, source = cg.fetch_quotes(["USD", "EUR", "GRA", "CEYREKALTIN"])

    assert quotes["USD"] == {"buying": 50.4027, "selling": 50.4936, "change": None}
    assert quotes["GRA"]["selling"] == pytest.approx(3539.0 * 50.4936 / cg.GRAMS_PER_TROY_OUNCE)
    assert "CEYREKALTIN" not in quotes  # no market price without Truncgil
    assert source.startswith("TCMB + ")
    assert updated_at.date().isoformat() == "2026-11-20"


def test_tcmb_rates_are_divided_by_unit(http):
    http[cg.TCMB_URL] = _response(content=TCMB_SAMPLE)
    cg.ITEMS["JPY"] = ("Japanese Yen", "currency")
    try:
        quotes, _ = cg.fetch_tcmb()
    finally:
        del cg.ITEMS["JPY"]

    assert quotes["JPY"]["selling"] == pytest.approx(0.3201)


def test_all_sources_down_returns_nothing(http):
    http[cg.TRUNCGIL_URL] = requests.ConnectionError("down")
    http[cg.TCMB_URL] = _response(status=503)
    http[cg.GOLD_SPOT_URL] = requests.ConnectionError("down")

    quotes, _, _ = cg.fetch_quotes(["USD", "GRA"])

    assert quotes == {}


# --- formatting ---

@pytest.mark.parametrize("value, group, language, expected", [
    (50.5752, "currency", "tr", "50,5752"),
    (50.5752, "currency", "en", "50.5752"),
    (5731.95, "gold", "tr", "5.731,95"),
    (9912, "gold", "tr", "9.912"),
    (199875.95, "gold", "en", "199,876"),
    (None, "gold", "tr", "—"),
])
def test_format_price(value, group, language, expected):
    assert cg.format_price(value, language, group) == expected


@pytest.mark.parametrize("value, language, expected", [
    (0.22, "tr", "▲ %0,22"),
    (-3.48, "en", "▼ 3.48%"),
    (0, "tr", "● %0,00"),
    (None, "tr", ""),
])
def test_format_change(value, language, expected):
    assert cg.format_change(value, language) == expected


def test_build_rows_groups_and_translates():
    quotes = {
        "USD": {"buying": 50.56, "selling": 50.57, "change": 0.22},
        "GRA": {"buying": 5730.97, "selling": 5731.95, "change": -3.48},
    }

    groups = cg.build_rows(["USD", "EUR", "GRA"], quotes, "tr")

    assert [g["label"] for g in groups] == ["Döviz", "Altın ve Değerli Metaller"]
    assert groups[0]["rows"][0]["label"] == "Amerikan Doları"
    assert groups[0]["rows"][0]["direction"] == "up"
    assert groups[1]["rows"][0]["direction"] == "down"
    assert len(groups[0]["rows"]) == 1  # EUR has no quote


def test_format_updated_converts_to_device_timezone():
    updated = pytz.timezone("Europe/Istanbul").localize(datetime(2026, 11, 22, 14, 41))

    text = cg.format_updated(updated, pytz.timezone("UTC"), "24h", "tr")

    assert text.endswith("11:41")
    assert "Kas" in text


# --- rendering ---

def test_render_template_in_turkish():
    config = MagicMock()
    config.get_config.side_effect = lambda key=None, default=None: {"language": "tr"}.get(key, default)
    i18n.init(config)
    try:
        plugin = cg.CurrencyGold({"id": "currency_gold"})
        groups = cg.build_rows(["USD", "CEYREKALTIN"], {
            "USD": {"buying": 50.56, "selling": 50.57, "change": 0.22},
            "CEYREKALTIN": {"buying": 9698, "selling": 9912, "change": -0.64},
        }, "tr")
        html = plugin.env.get_template("currency_gold.html").render(
            title="Döviz ve Altın", groups=groups, show_change=True, updated="22 Kas 14:41",
            source="Truncgil Finans", font_scale=1, row_count=4, plugin_settings={})
    finally:
        i18n.init(None)

    for text in ["Alış", "Satış", "Değişim", "Amerikan Doları", "Çeyrek Altın", "9.912", "▼ %0,64",
                 "Son güncelleme: 22 Kas 14:41", "Truncgil Finans"]:
        assert text in html, text


def test_generate_image_raises_when_no_data(monkeypatch):
    monkeypatch.setattr(cg, "fetch_quotes", lambda selected: ({}, None, ""))
    plugin = cg.CurrencyGold({"id": "currency_gold"})

    with pytest.raises(RuntimeError):
        plugin.generate_image({}, MagicMock())


# --- compact layout (short labels) ---

@pytest.mark.parametrize("style, dimensions, expected", [
    ("auto", (800, 480), False),
    ("auto", (397, 480), True),   # narrow dashboard widget
    ("auto", (800, 200), True),   # short dashboard widget
    ("full", (300, 200), False),
    ("short", (800, 480), True),
    ("bogus", (800, 480), False),
])
def test_use_compact_layout(style, dimensions, expected):
    assert cg.use_compact_layout(style, dimensions) is expected


def test_build_rows_short_labels():
    quotes = {key: {"buying": 1.0, "selling": 2.0, "change": 0.1} for key in ["USD", "EUR", "GRA", "CEYREKALTIN"]}

    groups = cg.build_rows(["USD", "EUR", "GRA", "CEYREKALTIN"], quotes, "tr", short_labels=True)

    assert [row["label"] for group in groups for row in group["rows"]] == ["$", "€", "Au gr", "Çeyrek"]
    assert set(cg.SHORT_LABELS) == set(cg.ITEMS)


def test_compact_render_hides_headings():
    config = MagicMock()
    config.get_config.side_effect = lambda key=None, default=None: {"language": "tr"}.get(key, default)
    i18n.init(config)
    try:
        plugin = cg.CurrencyGold({"id": "currency_gold"})
        groups = cg.build_rows(["USD", "GRA"], {
            "USD": {"buying": 50.56, "selling": 50.57, "change": 0.22},
            "GRA": {"buying": 5730.97, "selling": 5731.95, "change": -3.48},
        }, "tr", short_labels=True)
        html = plugin.env.get_template("currency_gold.html").render(
            title="Döviz ve Altın", compact=True, groups=groups, show_change=True, updated="22 Kas 14:41",
            source="Truncgil Finans", font_scale=1, row_count=2, plugin_settings={})
    finally:
        i18n.init(None)

    assert 'class="currency-gold compact"' in html
    assert ">$<" in html and ">Au gr<" in html
    for hidden in ["Döviz ve Altın", "group-row", "Son güncelleme", "Truncgil Finans"]:
        assert hidden not in html, hidden
    assert "22 Kas 14:41" in html
    assert "%0,22" not in html and "▲" in html  # compact change column shows only the arrow
