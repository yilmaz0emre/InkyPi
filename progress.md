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
  - ⚠️ Reviewed 2026-11-16 — has known bugs, see **Code review: power save (`ca7d9fb`)** below

### Upstream state (merged into `main`)

- Pillow reverted to 12.3.0 (#800), werkzeug 3.1.9, urllib3 2.8.0
- NASA APOD plugin moved to v2 API without key (#786)
- Streamed image responses closed properly (#781); apt refresh race fix in `update.sh` (#783)

## Code review: power save (`ca7d9fb`)

_Reviewed 2026-11-16. C1–C3, I4–I10 and minor/UI items fixed 2026-11-22 (hardware snapshot in `src/config/.power_save_state.json`, restored on wake and on boot; forced refresh after wake)._

### 🔴 Critical

- [x] **C1 — Boot safety can fail after power loss (violates core requirement).**
  `systemd-rfkill` persists rfkill state and restores it at boot. The device spends most of its time
  asleep with Wi-Fi blocked, so a battery cut during sleep can boot with Wi-Fi **still blocked** →
  web UI unreachable. `is_active = False` alone does not restore hardware.
  _Fix:_ in `PowerManager.__init__`, force-restore Wi-Fi (`rfkill unblock wifi`), Bluetooth, LEDs
  and CPU governor to normal state on startup.
- [x] **C2 — Wake does not guarantee a display refresh.**
  After waking, `RefreshTask` still goes through `_determine_next_plugin` →
  `PlaylistManager.should_refresh(..., plugin_cycle_interval_seconds)`. If sleep (e.g. 30 min) <
  plugin cycle interval (e.g. 1 h), Wi-Fi is cycled for nothing and the screen is not updated.
  `plugin_instance.should_refresh()` may also reuse a cached image.
  _Fix:_ force a refresh on every wake while power save is active (`PlaylistRefresh(force=True)`).
- [x] **C3 — LEDs stay permanently ON after wake.**
  `set_led_power` writes `brightness=0`, which clears the LED trigger (e.g. `mmc0`); writing `1` on
  wake leaves the LED solid on → extra consumption.
  _Fix:_ save `/sys/class/leds/*/trigger` before sleep, set `none`, restore original trigger on wake.

### 🟠 Important

- [x] **I4** — ~~CPU governor hard-coded back to `ondemand`~~ (fixed with C1 snapshot); Bluetooth re-enabled even if it was off
  before. Save original state and restore it (`exit_low_power_mode`).
- [x] **I5** — `ip link set wlan0 down` can confuse NetworkManager (Bookworm); `rfkill` alone is
  enough. No retry if reconnection fails (`disable_wifi` / `enable_wifi`).
- [x] **I6** — `wait_for_network` UDP `connect()` only checks for a route, not real connectivity/DNS.
- [x] **I7** — `/power_save/keep_awake`: `seconds` not validated/clamped (negative, huge values;
  non-numeric → 500).
- [x] **I8** — `/power_save/toggle`: string `"false"` is treated as truthy; response message uses raw
  input instead of `power_manager.is_active`.
- [x] **I9** — `save_settings` now always calls `signal_config_change()` (previously only on interval
  change). If Wi-Fi is kept on during sleep, saving settings cuts the sleep short.
- [x] **I10** — `ps_active_now` / `ps_active_now_submitted` handling in `save_settings` is dead code
  (no such form fields).

### 🟡 Minor / UI

- [x] `set_active(False)` does not reset `keep_awake_until` (status still showed 592 s after deactivation).
- [x] Remaining awake time is static (no countdown); main-page banner uses `alert()`.
- [x] English msgids contain Turkish (`"Awake Duration (Bekleme Süresi):"`, `"Sleep Duration (Uyku Süresi):"`)
  → Turkish words appear in the English UI. Use plain English msgids.
- [x] Real cycle = awake + sleep + reconnect/refresh time, not just sleep. Explain in UI.
- [x] Minimum awake is 10 s, but Wi-Fi reconnect takes ~10–25 s → raise minimum to ≥ 60 s.
- [x] `vcgencmd display_power` does not work under KMS (Bookworm). → HDMI control skipped and UI checkbox disabled when `dtoverlay=vc4-kms-v3d` is set.

### 📉 Power estimate correction

The earlier claim of "~75 % reduction, 35–40 mA" was too optimistic. Pi Zero 2 W idles at
~100–120 mA with Wi-Fi associated; with this software approach (Wi-Fi/LED/CPU) a realistic figure is
~70–90 mA → **~20–40 % saving**. Multi-fold battery life needs a hardware power cut with timed
wake (PiSugar / RTC-based power board).

### 🧪 Tests & git

- [x] Tests cover the `RefreshTask` power-save sleep/wake loop (`tests/test_refresh_task_power_save.py`),
  `/power_save/*` endpoints, boot-time hardware restore and forced refresh after wake.
- [x] Power save feature was committed and pushed to `feature/i18n-turkish`, mixing two features.
  Decided 2026-11-22: keep both in one branch while working only in the own fork; nothing goes to
  upstream `fatihak/InkyPi` for now. If proposed upstream later, split into `feature/i18n` and a stacked
  `feature/power-save` (power save UI depends on i18n) and drop `CLAUDE.md` / `progress.md`.

## Next / open

- [x] Fix power save review items C1–C3 and I4–I10
- [ ] Test power save mode on real Pi hardware (Wi-Fi reconnect via `wait_for_network`, rfkill, LED)
- [x] Review untranslated / fuzzy entries in `src/translations/tr/LC_MESSAGES/messages.po` (0 / 341 missing)
- [x] Check plugin render templates (rendered on the display) for remaining hard-coded English (GitHub metric titles, calendar all-day / more texts, RSS image alt)
- [x] Update docs (`docs/development.md`) with i18n workflow
- [x] Open PR `feature/i18n-turkish` → `main` in own fork: https://github.com/yilmaz0emre/InkyPi/pull/1
- [x] CI in own fork (`.github/workflows/tests.yml`): pytest on Python 3.11 + 3.13 and translation catalog
  check (`scripts/check_translations.py`, locally `bash scripts/i18n.sh check`). First run on PR #1 green.

## Currency & Gold plugin (`feature/currency-gold`)

- [x] New plugin `src/plugins/currency_gold/`: TRY exchange rates and gold prices on the e-ink display
  - Source: Truncgil Finans (`finans.truncgil.com/v4/today.json`, no key, unofficial). Fallback: TCMB
    `today.xml` for currencies + gram gold calculated from XAU/USD (`api.gold-api.com`) × USD/TRY
  - Selectable items (default USD, EUR, GBP, gram gold, quarter gold), buying / selling / daily change,
    last update in device timezone, auto-fit text for long lists; Turkish + English
  - `ONS`, BIST100, Brent and Bitcoin from Truncgil are skipped (return 0 / out of scope)
  - Tests: `tests/test_currency_gold.py` (parsing, fallback, formatting, Turkish render)
- [ ] Check on real hardware: readability on the actual panel (colour vs. black/white), vertical orientation

## Dashboard plugin (`feature/dashboard`)

- [x] `src/plugins/dashboard/`: several saved plugin instances on one screen, with a web layout editor
  - Widgets reference saved instances; new per-instance flag **"Only use inside dashboards"**
    (`PluginInstance.dashboard_only`) keeps them out of the playlist rotation
  - **Single browser render:** `render_image()` has a capture mode (`capture_rendered_html()` in
    `base_plugin.py`) returning the HTML instead of screenshotting; each HTML widget is loaded in its own
    `<iframe src=file://…>` (not `srcdoc`, which forces standards mode and broke the weather layout),
    PIL widgets are embedded as data URIs, then one screenshot. 3 widgets ≈ 1–2 s on a Mac
  - Editor: 12×12 grid, drag to move, corner to resize, overlap warning, gap + borders; **Preview**
    via new generic `POST /preview_plugin` (returns PNG, display untouched)
  - A failing widget shows an error box instead of failing the whole dashboard
  - Tests: `tests/test_dashboard.py`; editor drag/resize verified in headless Chrome
- [x] Any installed plugin can be added: editor lists saved widgets plus "Create new widget" for every
  plugin (opens its page with `?for_dashboard=1`, "Only use inside dashboards" pre-ticked); the list
  refreshes on tab focus via `GET /dashboard_instances`, the unsaved layout is kept
- [x] Currency & Gold compact layout: label style auto/full/short; auto switches below 450×260 px to
  symbols ($, €, Au gr, Çeyrek), no title/group headings, arrow-only change column
- [x] Currency & Gold strip layout ("like a TV ticker"): one row of short label + selling price + arrow;
  auto for boxes ≥ 3× wider than tall and < 200 px high (e.g. 12×2); wraps onto 2 rows when that gives
  bigger text (many items); vertical padding scaled to the box so 12×1 strips still render
- [x] Weather compact layout (layout style auto/full/short): location, big icon + temperature, today's
  ▲high ▼low, optional 2–3 day mini forecast when the box is tall enough. Shared size rule in
  `src/utils/compact_layout.py` (also used by Currency & Gold)
- [ ] Other plugins (calendar, RSS, to-do, ...) have no compact layout yet
- Note: standalone renders narrower than ~500 px get cut off because headless Chrome will not open a
  narrower window; inside a dashboard each widget is an exact-size iframe, so this does not apply there
- [ ] Measure render time on a Pi Zero 2 W

## Notes

- Use `venv/` for tests; system `python3` lacks pytest.
- Always commit `.po` and `.mo` together.
