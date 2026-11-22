#!/usr/bin/env bash
set -e

# InkyPi i18n management script
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

VENV_PYBABEL="${BASE_DIR}/venv/bin/pybabel"
PYBABEL="${PYBABEL:-$VENV_PYBABEL}"

if [ ! -f "$PYBABEL" ]; then
    PYBABEL="pybabel"
fi

case "$1" in
    extract)
        echo "Extracting translatable messages..."
        "$PYBABEL" extract -F babel.cfg -k _ -k gettext -k ngettext:1,2 -k N_ -o src/translations/messages.pot .
        echo "Done. Updated src/translations/messages.pot"
        ;;
    update)
        echo "Updating translation catalogs..."
        "$PYBABEL" update -i src/translations/messages.pot -d src/translations
        echo "Done."
        ;;
    compile)
        echo "Compiling translation catalogs (.po -> .mo)..."
        "$PYBABEL" compile -d src/translations
        echo "Done."
        ;;
    all)
        "$0" extract
        "$0" update
        "$0" compile
        ;;
    check)
        # Same check as CI: no missing/untranslated/fuzzy strings and .mo files up to date
        PYTHON="${BASE_DIR}/venv/bin/python"
        [ -x "$PYTHON" ] || PYTHON="python3"
        PATH="$(dirname "$PYTHON"):$PATH" "$PYTHON" scripts/check_translations.py
        ;;
    *)
        echo "Usage: $0 {extract|update|compile|all|check}"
        exit 1
        ;;
esac
