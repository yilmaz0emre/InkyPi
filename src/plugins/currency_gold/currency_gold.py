"""Currency & Gold plugin: Turkish lira exchange rates and gold prices.

Primary source is Truncgil Finans (no API key, includes Turkish market gold prices such as
"çeyrek altın"). If it is unreachable, currencies fall back to the official TCMB rates and
gram gold is derived from the gold spot price (XAU/USD) and USD/TRY.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import pytz
from babel.numbers import format_decimal

from plugins.base_plugin.base_plugin import BasePlugin
from utils import i18n
from utils.http_client import get_http_session
from utils.i18n import N_

logger = logging.getLogger(__name__)

TRUNCGIL_URL = "https://finans.truncgil.com/v4/today.json"
TCMB_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
GOLD_SPOT_URL = "https://api.gold-api.com/price/XAU"
SOURCE_TIMEZONE = pytz.timezone("Europe/Istanbul")
GRAMS_PER_TROY_OUNCE = 31.1034768
REQUEST_TIMEOUT = 15

# key (Truncgil id) -> (display label, group). Order here is the display order.
ITEMS = {
    "USD": (N_("US Dollar"), "currency"),
    "EUR": (N_("Euro"), "currency"),
    "GBP": (N_("British Pound"), "currency"),
    "CHF": (N_("Swiss Franc"), "currency"),
    "CAD": (N_("Canadian Dollar"), "currency"),
    "AUD": (N_("Australian Dollar"), "currency"),
    "SAR": (N_("Saudi Riyal"), "currency"),
    "CNY": (N_("Chinese Yuan"), "currency"),
    "RUB": (N_("Russian Ruble"), "currency"),
    "GRA": (N_("Gram Gold"), "gold"),
    "HAS": (N_("Gram Gold (24k Pure)"), "gold"),
    "CEYREKALTIN": (N_("Quarter Gold"), "gold"),
    "YARIMALTIN": (N_("Half Gold"), "gold"),
    "TAMALTIN": (N_("Full Gold"), "gold"),
    "CUMHURIYETALTINI": (N_("Republic Gold"), "gold"),
    "YIA": (N_("22k Bracelet Gold (gram)"), "gold"),
    "18AYARALTIN": (N_("18k Gold (gram)"), "gold"),
    "14AYARALTIN": (N_("14k Gold (gram)"), "gold"),
    "GUMUS": (N_("Silver (gram)"), "gold"),
}
GROUP_LABELS = {"currency": N_("Currencies"), "gold": N_("Gold & Precious Metals")}
DEFAULT_ITEMS = ["USD", "EUR", "GBP", "GRA", "CEYREKALTIN"]

FONT_SIZES = {
    "x-small": 0.7,
    "small": 0.85,
    "normal": 1,
    "large": 1.15,
    "x-large": 1.3,
}


class CurrencyGold(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        template_params["currency_gold_items"] = [
            {"key": key, "label": label, "group": group} for key, (label, group) in ITEMS.items()
        ]
        template_params["currency_gold_groups"] = GROUP_LABELS
        template_params["currency_gold_defaults"] = DEFAULT_ITEMS
        return template_params

    def generate_image(self, settings, device_config):
        selected = get_selected_items(settings)
        quotes, updated_at, source = fetch_quotes(selected)
        if not quotes:
            raise RuntimeError(i18n.gettext("Could not fetch exchange rates or gold prices."))

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = pytz.timezone(device_config.get_config("timezone", default="UTC"))
        time_format = device_config.get_config("time_format", default="24h")
        language = i18n.get_language()

        template_params = {
            "title": settings.get("title") or i18n.gettext("Currency & Gold"),
            "groups": build_rows(selected, quotes, language),
            "show_change": settings.get("showChange", "true") != "false",
            "updated": format_updated(updated_at, timezone, time_format, language),
            "source": source,
            "font_scale": FONT_SIZES.get(settings.get("fontSize", "normal"), 1),
            "plugin_settings": settings,
        }
        template_params["row_count"] = sum(len(group["rows"]) + 1 for group in template_params["groups"])

        return self.render_image(dimensions, "currency_gold.html", "currency_gold.css", template_params)


def get_selected_items(settings):
    """Return selected item keys in display order, falling back to the defaults."""
    selected = settings.get("items[]") or DEFAULT_ITEMS
    if isinstance(selected, str):
        selected = [selected]
    return [key for key in ITEMS if key in selected] or list(DEFAULT_ITEMS)


def fetch_quotes(selected):
    """Return ({key: {"buying", "selling", "change"}}, updated_at (aware datetime), source name)."""
    try:
        return fetch_truncgil(selected)
    except Exception as e:
        logger.warning(f"Truncgil Finans unavailable, using fallback sources: {e}")
    return fetch_fallback(selected)


def fetch_truncgil(selected):
    response = get_http_session().get(TRUNCGIL_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    quotes = {}
    for key in selected:
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        quote = {
            "buying": _positive(entry.get("Buying")),
            "selling": _positive(entry.get("Selling")),
            "change": _number(entry.get("Change")),
        }
        if quote["buying"] or quote["selling"]:
            quotes[key] = quote
    if not quotes:
        raise ValueError("no usable prices in response")

    updated_at = None
    try:
        updated_at = SOURCE_TIMEZONE.localize(datetime.strptime(data.get("Update_Date", ""), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        logger.warning(f"Unexpected Truncgil Update_Date: {data.get('Update_Date')!r}")
    return quotes, updated_at, "Truncgil Finans"


def fetch_fallback(selected):
    """Official TCMB rates for currencies; gram gold derived from the XAU/USD spot price."""
    quotes, updated_at, sources = {}, None, []
    usd_try = None
    try:
        tcmb_quotes, updated_at = fetch_tcmb()
        usd_try = tcmb_quotes.get("USD", {}).get("selling")
        for key in selected:
            if key in tcmb_quotes:
                quotes[key] = tcmb_quotes[key]
        sources.append("TCMB")
    except Exception as e:
        logger.error(f"TCMB rates unavailable: {e}")

    if "GRA" in selected and usd_try:
        try:
            response = get_http_session().get(GOLD_SPOT_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            ounce_usd = _positive(response.json().get("price"))
            if ounce_usd:
                gram_try = ounce_usd * usd_try / GRAMS_PER_TROY_OUNCE
                quotes["GRA"] = {"buying": None, "selling": gram_try, "change": None}
                sources.append(i18n.gettext("spot gold (calculated)"))
        except Exception as e:
            logger.error(f"Gold spot price unavailable: {e}")

    return quotes, updated_at, " + ".join(sources)


def fetch_tcmb():
    response = get_http_session().get(TCMB_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    quotes = {}
    for currency in root.findall("Currency"):
        code = currency.get("CurrencyCode")
        unit = _positive(currency.findtext("Unit")) or 1
        buying = _positive(currency.findtext("ForexBuying"))
        selling = _positive(currency.findtext("ForexSelling"))
        if code in ITEMS and (buying or selling):
            quotes[code] = {
                "buying": buying / unit if buying else None,
                "selling": selling / unit if selling else None,
                "change": None,
            }

    updated_at = None
    try:
        # TCMB publishes once per business day around 15:30 Istanbul time
        updated_at = SOURCE_TIMEZONE.localize(datetime.strptime(root.get("Tarih", ""), "%d.%m.%Y").replace(hour=15, minute=30))
    except ValueError:
        pass
    return quotes, updated_at


def build_rows(selected, quotes, language):
    """Group available quotes for the template, with localized labels and numbers."""
    groups = []
    for group_key, group_label in GROUP_LABELS.items():
        rows = []
        for key in selected:
            label, item_group = ITEMS[key]
            if item_group != group_key or key not in quotes:
                continue
            quote = quotes[key]
            change = quote.get("change")
            rows.append({
                "key": key,
                "label": i18n.gettext(label, language=language),
                "buying": format_price(quote.get("buying"), language, item_group),
                "selling": format_price(quote.get("selling"), language, item_group),
                "change": format_change(change, language),
                "direction": "flat" if change is None or change == 0 else ("up" if change > 0 else "down"),
            })
        if rows:
            groups.append({"label": i18n.gettext(group_label, language=language), "rows": rows})
    return groups


def format_price(value, language, group="currency"):
    """Exchange rates get 4 decimals (as quoted in Turkey); gold 2, or none for whole / large prices."""
    if value is None:
        return "—"
    if group == "currency":
        pattern = "#,##0.0000"
    elif value >= 10000 or value == int(value):
        pattern = "#,##0"
    else:
        pattern = "#,##0.00"
    return format_decimal(value, format=pattern, locale=language)


def format_change(value, language):
    if value is None:
        return ""
    arrow = "▲" if value > 0 else ("▼" if value < 0 else "●")
    return f"{arrow} %{format_decimal(abs(value), format='0.00', locale=language)}" if language == "tr" \
        else f"{arrow} {format_decimal(abs(value), format='0.00', locale=language)}%"


def format_updated(updated_at, timezone, time_format, language):
    if not updated_at:
        return ""
    local = updated_at.astimezone(timezone)
    return f"{i18n.format_date(local, skeleton='dMMM', language=language)} {i18n.format_time(local, time_format, language=language)}"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive(value):
    number = _number(value)
    return number if number and number > 0 else None
