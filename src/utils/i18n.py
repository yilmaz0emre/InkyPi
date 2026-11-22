"""Internationalization (i18n) helpers for InkyPi.

The active language is stored in the device config under the ``language`` key
and applies to both the web interface and the content rendered on the display.

Two contexts need translations:

* Flask requests: handled by Flask-Babel (see ``init_app``), which exposes
  ``_()`` / ``gettext()`` to the web templates.
* Background rendering (refresh task / plugins): runs outside of a request,
  so plugins use ``gettext()`` / ``format_date()`` from this module, which
  read the language straight from the device config.
"""

import logging
import os
from functools import lru_cache

from babel import Locale
from babel.dates import format_skeleton, format_time as babel_format_time
from babel.support import NullTranslations, Translations

logger = logging.getLogger(__name__)

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSLATIONS_DIR = os.path.join(SRC_DIR, "translations")

DEFAULT_LANGUAGE = "en"
# language code -> name shown in the language selector (in its own language)
SUPPORTED_LANGUAGES = {
    "en": "English",
    "tr": "Türkçe",
}

_device_config = None


def N_(message):
    """Mark a string for extraction without translating it.

    Use for module-level constants that are translated later, at render time,
    with ``_()`` in a template or ``gettext()`` in Python.
    """
    return message


def init(device_config):
    """Register the device config so the active language can be resolved."""
    global _device_config
    _device_config = device_config


def init_app(app, device_config):
    """Set up Flask-Babel for the web interface."""
    from flask_babel import Babel

    init(device_config)
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = TRANSLATIONS_DIR
    app.config["BABEL_DEFAULT_LOCALE"] = DEFAULT_LANGUAGE
    Babel(app, locale_selector=get_language)

    @app.context_processor
    def inject_i18n():
        return {
            "current_language": get_language(),
            "supported_languages": SUPPORTED_LANGUAGES,
        }


def normalize_language(language):
    """Return a supported language code, falling back to the default."""
    if language in SUPPORTED_LANGUAGES:
        return language
    return DEFAULT_LANGUAGE


def get_language(device_config=None):
    """Return the active language code from the device config."""
    config = device_config or _device_config
    if config is None:
        return DEFAULT_LANGUAGE
    return normalize_language(config.get_config("language", default=DEFAULT_LANGUAGE))


@lru_cache(maxsize=None)
def get_translations(language):
    """Load (and cache) the compiled translations for a language."""
    language = normalize_language(language)
    if language == DEFAULT_LANGUAGE:
        return NullTranslations()
    translations = Translations.load(TRANSLATIONS_DIR, [language])
    if not isinstance(translations, Translations):
        logger.warning(f"No compiled translations found for '{language}', falling back to English")
    return translations


def gettext(message, language=None, **variables):
    """Translate a message outside of a Flask request (e.g. while rendering plugins)."""
    translated = get_translations(language or get_language()).gettext(message)
    return translated % variables if variables else translated


def ngettext(singular, plural, num, language=None, **variables):
    """Plural-aware translation outside of a Flask request."""
    variables.setdefault("num", num)
    translated = get_translations(language or get_language()).ngettext(singular, plural, num)
    return translated % variables


def format_date(dt, skeleton="MMMMEEEEd", language=None):
    """Format a date using a CLDR skeleton, localized for the active language.

    Unlike ``strftime``, skeletons adapt the order and wording to the locale,
    e.g. ``MMMMEEEEd`` -> "Saturday, November 14" (en) / "14 Kasım Cumartesi" (tr).
    Common skeletons: ``EEE`` (short weekday), ``MMM`` (short month),
    ``yMMMMd`` (full date), ``MMMMEEEEd`` (weekday + day + month).
    """
    return format_skeleton(skeleton, dt, locale=language or get_language())


def format_time(dt, time_format="24h", language=None):
    """Format a time as 24h ("14:05") or 12h ("2:05 PM") for the active language."""
    pattern = "HH:mm" if time_format == "24h" else "h:mm a"
    return babel_format_time(dt, pattern, locale=language or get_language())


def get_period_label(dt, language=None):
    """Return the localized AM/PM label for a datetime."""
    locale = Locale.parse(language or get_language())
    period = "pm" if dt.hour >= 12 else "am"
    return locale.day_periods["format"]["abbreviated"].get(period, period.upper())
