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

_Reviewed 2026-11-16. C1–C3 fixed 2026-11-22 (hardware snapshot in `src/config/.power_save_state.json`, restored on wake and on boot; forced refresh after wake)._

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

- [ ] **I4** — ~~CPU governor hard-coded back to `ondemand`~~ (fixed with C1 snapshot); Bluetooth re-enabled even if it was off
  before. Save original state and restore it (`exit_low_power_mode`).
- [ ] **I5** — `ip link set wlan0 down` can confuse NetworkManager (Bookworm); `rfkill` alone is
  enough. No retry if reconnection fails (`disable_wifi` / `enable_wifi`).
- [ ] **I6** — `wait_for_network` UDP `connect()` only checks for a route, not real connectivity/DNS.
- [ ] **I7** — `/power_save/keep_awake`: `seconds` not validated/clamped (negative, huge values;
  non-numeric → 500).
- [ ] **I8** — `/power_save/toggle`: string `"false"` is treated as truthy; response message uses raw
  input instead of `power_manager.is_active`.
- [ ] **I9** — `save_settings` now always calls `signal_config_change()` (previously only on interval
  change). If Wi-Fi is kept on during sleep, saving settings cuts the sleep short.
- [ ] **I10** — `ps_active_now` / `ps_active_now_submitted` handling in `save_settings` is dead code
  (no such form fields).

### 🟡 Minor / UI

- [ ] `set_active(False)` does not reset `keep_awake_until` (status still showed 592 s after deactivation).
- [ ] Remaining awake time is static (no countdown); main-page banner uses `alert()`.
- [ ] English msgids contain Turkish (`"Awake Duration (Bekleme Süresi):"`, `"Sleep Duration (Uyku Süresi):"`)
  → Turkish words appear in the English UI. Use plain English msgids.
- [ ] Real cycle = awake + sleep + reconnect/refresh time, not just sleep. Explain in UI.
- [ ] Minimum awake is 10 s, but Wi-Fi reconnect takes ~10–25 s → raise minimum to ≥ 60 s.
- [ ] `vcgencmd display_power` does not work under KMS (Bookworm).

### 📉 Power estimate correction

The earlier claim of "~75 % reduction, 35–40 mA" was too optimistic. Pi Zero 2 W idles at
~100–120 mA with Wi-Fi associated; with this software approach (Wi-Fi/LED/CPU) a realistic figure is
~70–90 mA → **~20–40 % saving**. Multi-fold battery life needs a hardware power cut with timed
wake (PiSugar / RTC-based power board).

### 🧪 Tests & git

- [ ] Tests only cover `PowerManager` in mock mode. Add tests for the `RefreshTask` power-save loop,
  the `/power_save/*` endpoints and boot-time hardware restore (C1).
- [ ] Power save feature was committed and pushed to `feature/i18n-turkish`, mixing two features.
  Consider moving it to a separate `feature/power-save` branch.

## Next / open

- [ ] Fix power save review items I4–I10 (C1–C3 done, see above)
- [ ] Test power save mode on real Pi hardware (Wi-Fi reconnect via `wait_for_network`, rfkill, LED)
- [ ] Review untranslated / fuzzy entries in `src/translations/tr/LC_MESSAGES/messages.po`
- [ ] Check plugin render templates (rendered on the display) for remaining hard-coded English
- [ ] Update docs (`docs/development.md`) with i18n workflow
- [ ] Open PR `feature/i18n-turkish` → `main`

## Notes

- Use `venv/` for tests; system `python3` lacks pytest.
- Always commit `.po` and `.mo` together.
