#!/usr/bin/env python3
"""Fail if translation catalogs are out of date.

Checks every src/translations/<lang>/LC_MESSAGES/messages.po for:
  * strings in the source code that are missing from the catalog (run `scripts/i18n.sh all`)
  * untranslated or fuzzy entries (fuzzy entries are ignored at runtime)
  * a committed messages.mo that does not match its messages.po (run `scripts/i18n.sh compile`)

Usage: python scripts/check_translations.py   (from the repository root)
"""
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = ROOT / "src" / "translations"
# Keep in sync with the `extract` command in scripts/i18n.sh
EXTRACT_ARGS = ["extract", "-F", "babel.cfg", "-k", "_", "-k", "gettext", "-k", "ngettext:1,2", "-k", "N_", "--no-location"]


def extract_message_ids():
    with tempfile.TemporaryDirectory() as tmp:
        pot_path = os.path.join(tmp, "messages.pot")
        subprocess.run(["pybabel", *EXTRACT_ARGS, "-o", pot_path, "."], cwd=ROOT, check=True, capture_output=True)
        with open(pot_path, "rb") as f:
            return {message.id for message in read_po(f) if message.id}


def check_catalog(po_path, source_ids):
    errors = []
    with open(po_path, "rb") as f:
        catalog = read_po(f)

    catalog_ids = {message.id for message in catalog if message.id}
    for message_id in sorted(source_ids - catalog_ids, key=str):
        errors.append(f"missing from catalog: {message_id!r}")

    for message in catalog:
        if not message.id:
            continue
        if message.fuzzy:
            errors.append(f"fuzzy: {message.id!r} -> {message.string!r}")
        elif not message.string or (isinstance(message.string, tuple) and not all(message.string)):
            errors.append(f"untranslated: {message.id!r}")

    mo_path = po_path.with_suffix(".mo")
    expected = io.BytesIO()
    write_mo(expected, catalog)
    if not mo_path.exists():
        errors.append(f"{mo_path.relative_to(ROOT)} is missing")
    elif mo_path.read_bytes() != expected.getvalue():
        errors.append(f"{mo_path.relative_to(ROOT)} is stale; run `bash scripts/i18n.sh compile` and commit it")

    return errors


def main():
    source_ids = extract_message_ids()
    po_paths = sorted(TRANSLATIONS_DIR.glob("*/LC_MESSAGES/messages.po"))
    if not po_paths:
        print("No translation catalogs found.")
        return 1

    failed = False
    for po_path in po_paths:
        errors = check_catalog(po_path, source_ids)
        name = po_path.relative_to(ROOT)
        if errors:
            failed = True
            print(f"✗ {name}: {len(errors)} problem(s)")
            for error in errors:
                print(f"    {error}")
        else:
            print(f"✓ {name}: {len(source_ids)} strings, all translated, .mo up to date")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
