# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Fork of **InkyPi** (upstream: `fatihak/InkyPi`) — a Raspberry Pi–driven E-Ink display with a Flask web UI for configuring plugins, playlists and refresh schedules. Supports Pimoroni Inky and Waveshare panels.

This fork adds:
- **Turkish/English i18n** via Flask-Babel (`feature/i18n-turkish` branch)
- **Low-power battery save mode** (`src/utils/power_manager.py`)

Current work tracking: see [progress.md](./progress.md).

## Commands

```bash
source venv/bin/activate
python src/inkypi.py --dev          # dev server on http://localhost:8080, mock display
venv/bin/python -m pytest -q        # tests (pytest.ini: testpaths=tests, pythonpath=src)
bash scripts/i18n.sh all            # extract -> update -> compile translations
bash install/update_vendors.sh      # fetch frontend vendor assets (first dev setup)
```

Dev mode renders to `mock_display_output/latest.png` instead of hardware. Use the project `venv/` — the system `python3` has no pytest.

## Layout

```
src/
  inkypi.py            # entrypoint: Flask app, Config, i18n, PowerManager, DisplayManager, RefreshTask
  config.py, model.py  # device config (JSON) + playlist/refresh models
  refresh_task.py      # background loop: plugin cycling, playlists, power-save sleep/wake
  blueprints/          # main, settings, plugin, playlist, apikeys routes
  display/             # abstract_display, inky_display, waveshare_display, mock_display
  plugins/<name>/      # <name>.py + plugin-info.json + settings.html (+ render/, icon.png)
  templates/           # Jinja pages (inky.html, settings.html, plugin.html, playlist.html…)
  translations/tr/LC_MESSAGES/messages.{po,mo}
  utils/               # i18n.py, i18n_strings.py, power_manager.py, image_*.py, http_client.py
install/               # install.sh, update.sh, requirements*.txt, systemd service
docs/                  # development.md, building_plugins.md, …
tests/                 # test_i18n.py, test_power_manager.py, test_model.py, test_image_loader.py
```

Shared app objects live in `app.config`: `DEVICE_CONFIG`, `DISPLAY_MANAGER`, `REFRESH_TASK`, `POWER_MANAGER`. Read optional ones with `.get()` (blueprints do `current_app.config.get('POWER_MANAGER')`).

## i18n conventions

- Default language `en`; active language is the `language` key in device config (`utils/i18n.get_language`). Supported languages are in `SUPPORTED_LANGUAGES` in `src/utils/i18n.py`.
- Python: `from flask_babel import gettext as _` in request context; outside a request (plugins, refresh thread) use `utils.i18n.gettext` / `format_date` / `format_time`.
- Use named placeholders: `_("Saved %(n)s key(s)", n=count)` — never f-strings inside `_()`.
- Templates: `{{ _("...") }}`. Strings that only exist in JSON or are built dynamically (e.g. plugin display names) get registered with `N_()` in `src/utils/i18n_strings.py`.
- After adding/changing strings: `bash scripts/i18n.sh all`, fill in Turkish in `messages.po`, recompile. **`messages.mo` is committed on purpose** (devices deploy without a build step) — always commit `.po` and `.mo` together.
- `babel.cfg` scans `src/**.py`, `src/templates/**.html`, `src/plugins/**.html`.

## Power save mode

- `PowerManager` is **always inactive on boot / power loss** (runtime-only flag) so the device stays reachable over Wi-Fi. Don't persist `is_active`.
- Config lives under `power_save_config` in device config (awake/sleep duration + unit, toggles for Wi-Fi, Bluetooth, HDMI, LED).
- `RefreshTask` enters low-power after the awake window and exits before the next refresh; a manual update extends `keep_awake_until`.
- Hardware calls (rfkill, vcgencmd, LED sysfs, cpufreq) are skipped when not on a Pi or in dev mode — keep that guard for anything new.

## Conventions

- Conventional commits (`feat(scope):`, `fix:`, `build(i18n):`).
- Keep upstream-compatible structure so merges from `fatihak/InkyPi` stay easy; avoid gratuitous reformatting of upstream files.
- Pillow is pinned to 12.3.0 (13.x breaks Inky/caption rendering — upstream PR #800).
