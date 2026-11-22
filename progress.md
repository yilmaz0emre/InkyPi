# Progress

_Last updated: 2026-11-22_

## Current branch: `feature/i18n-turkish`

3 commits ahead of `main` (all 2026-11-14). Test suite: **41 passed** (`venv/bin/python -m pytest -q`).

### Done

- [x] **i18n — Turkish + English** (`3dfbf33`)
  - Flask-Babel integration (`src/utils/i18n.py`), language selector in settings, locale-aware date/time helpers
  - All blueprints, core templates and every plugin `settings.html` wrapped in `_()`
  - `scripts/i18n.sh` (extract/update/compile/all), `babel.cfg`, `src/utils/i18n_strings.py` for JSON-defined names
  - `tests/test_i18n.py`
- [x] **Compiled catalog tracked in git** (`c8a4e7e`) — `messages.mo` committed for embedded deployment
- [x] **Low-power battery save mode** (`ca7d9fb`)
  - `src/utils/power_manager.py`: awake/sleep cycle; disables Wi-Fi, Bluetooth, HDMI, LED; CPU governor
  - Boot safety: always inactive after boot/power loss
  - Settings UI section + status badge on main page, endpoints `/power_save/toggle`, `/power_save/keep_awake`, `/power_save/status` in `settings.py`
  - `RefreshTask` integration (enter/exit low power, keep-awake after manual refresh)
  - `tests/test_power_manager.py`

### Upstream state (merged into `main`)

- Pillow reverted to 12.3.0 (#800), werkzeug 3.1.9, urllib3 2.8.0
- NASA APOD plugin moved to v2 API without key (#786)
- Streamed image responses closed properly (#781); apt refresh race fix in `update.sh` (#783)

## Next / open

- [ ] Test power save mode on real Pi hardware (Wi-Fi reconnect via `wait_for_network`, rfkill, LED)
- [ ] Review untranslated / fuzzy entries in `src/translations/tr/LC_MESSAGES/messages.po`
- [ ] Check plugin render templates (rendered on the display) for remaining hard-coded English
- [ ] Update docs (`docs/development.md`) with i18n workflow
- [ ] Open PR `feature/i18n-turkish` → `main`

## Notes

- Use `venv/` for tests; system `python3` lacks pytest.
- Always commit `.po` and `.mo` together.
