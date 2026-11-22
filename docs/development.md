# InkyPi Development Quick Start

## Development Without Hardware

The `--dev` flag enables complete development without requiring:

- Raspberry Pi hardware
- Physical e-ink displays (Inky pHAT/wHAT or Waveshare)
- Root privileges or GPIO access
- Linux-specific features (systemd)

Works on **macOS**, **Linux**, and **Windows** - no hardware needed!

## Setup
<table>
<tr>
<td>
Traditional setup method

```bash
# 1. Clone and setup
git clone https://github.com/fatihak/InkyPi.git
cd InkyPi

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install Python dependencies and run
pip install -r install/requirements-dev.txt
bash install/update_vendors.sh
python src/inkypi.py --dev
```

</td>
<td>
Alternative method using devbox - works on macOS / any Linux distro / WSL2

```bash
setopt INTERACTIVE_COMMENTS # enable interactive comments in zsh

# 1. Install devbox and direnv (direnv is optional but recommended)
command -v devbox >/dev/null || curl -fsSL https://get.jetify.com/devbox | bash
command -v direnv >/dev/null || nix profile install "nixpkgs#direnv" \
  && eval "$(direnv hook ${SHELL##*/})" >> ~/.${SHELL##*/}rc \
  && source ~/.${SHELL##*/}rc # If not already present: install direnv, add hooks to shell rc and activate

# 2. Clone and setup
git clone https://github.com/fatihak/InkyPi.git
cd InkyPi # direnv reads .envrc -> runs devbox shell -> installs deps & activates venv

# 3. Run InkyPi in developer mode via devbox
devbox run dev # alternatively run `devbox shell` and then run `python src/inkypi.py --dev`
```

</td>
</tr>
</table>

**That's it!** Open http://localhost:8080 and start developing.

## What You Can Do

- **Develop plugins** - Create new plugins without hardware (no Raspberry Pi, nor physical displays)
- **Test UI changes** - Instant feedback on web interface modifications  
- **Debug issues** - Full error messages in terminal
- **Verify rendering** - Check output in `mock_display_output/latest.png`
- **Cross-platform development** - Works on macOS, Linux, Windows

## Essential Commands

<table>
<tr>
<td>

Traditional activation method

```bash
source venv/bin/activate             # Activate virtual environment
python src/inkypi.py --dev           # Start development server
deactivate                           # Exit virtual environment
```

</td>
<td>
devbox / direnv method

```bash
devbox run dev # run InkyPi in dev mode, terminating deactivates `devbox shell`

# direnv will activate / deactivate `devbox shell` automatically when entering
# and leaving the project directory (provided `direnv allow` has run once)... 
# Otherwise to manually activate / deactivate:
devbox shell                         # Installs deps, and activates Python virtual environment
python src/inkypi.py --dev           # Start development server
exit                                 # Exit devbox shell and deactivates Python virtual environment
```

</td>
</tr>
</table>

## Development Tips

1. **Check rendered output**: Images are saved to `mock_display_output/`
2. **Plugin development**: Copy an existing plugin as template (e.g., `clock/`)
3. **Configuration**: Edit `src/config/device_dev.json` for display settings
4. **Hot reload**: Restart server to see code changes

## Testing Your Changes

1. Configure a plugin through the web UI
2. Click "Display" button
3. Check `mock_display_output/latest.png` for result
4. Iterate quickly without deployment

Run the unit tests with:

```bash
python -m pytest -q    # uses pytest.ini (testpaths=tests, pythonpath=src)
```

## Translations (i18n)

The web UI and plugin output are translated with [Flask-Babel](https://python-babel.github.io/flask-babel/). English is the default; the active language is the `language` key in the device config (selected under **Settings**). Supported languages are listed in `SUPPORTED_LANGUAGES` in `src/utils/i18n.py`, and catalogs live in `src/translations/<lang>/LC_MESSAGES/`.

### Marking strings

| Where | How |
| --- | --- |
| Web UI templates (`src/templates/`, plugin `settings.html`) | `{{ _('Save') }}` |
| Plugin render templates (`render/*.html`) | `{{ _('more') }}` — rendered with the device language |
| Flask routes (request context) | `from flask_babel import gettext as _` |
| Plugins / background thread (no request) | `from utils.i18n import gettext` (also `format_date`, `format_time`) |
| Values defined in Python or JSON but translated later | Wrap with `N_('...')` so they are extracted, then translate where shown, e.g. `{{ _(metric.title) }}`. JSON-only strings (plugin display names) are registered in `src/utils/i18n_strings.py`. |

Use named placeholders, never f-strings inside `_()`:

```python
_("Saved %(count)s API key(s)", count=len(keys))
```

### Updating catalogs

```bash
bash scripts/i18n.sh extract   # scan sources -> src/translations/messages.pot
bash scripts/i18n.sh update    # merge new strings into each messages.po
# translate the new / fuzzy entries in src/translations/<lang>/LC_MESSAGES/messages.po
bash scripts/i18n.sh compile   # messages.po -> messages.mo
bash scripts/i18n.sh all       # extract + update + compile in one step
```

`update` pre-fills new entries with *fuzzy* guesses that are often wrong — always review entries marked `#, fuzzy`, then remove the flag. Fuzzy entries are not used at runtime.

The compiled `messages.mo` is committed on purpose so devices can deploy without a build step. **Always commit `messages.po` and `messages.mo` together.**

### Adding a language

1. `pybabel init -i src/translations/messages.pot -d src/translations -l <lang>`
2. Add the code and its native name to `SUPPORTED_LANGUAGES` in `src/utils/i18n.py`
3. Translate `messages.po`, run `bash scripts/i18n.sh compile`, and commit both files

## Other Requirements

InkyPi relies on system packages for some features, which are normally installed via the `install.sh` script. **(Skip if using devbox method)**

### Linux
**(Skip this section if using devbox method)**

The required packages can be found in this file: 

https://github.com/fatihak/InkyPi/blob/main/install/debian-requirements.txt

Use your favourite package manager (such as `apt`) to install them.

### Chromium or Google Chrome browser

InkyPi uses `--headless` mode to rendering HTML templates to PNG images using a Chrome-like browser.  

Different platforms have different available browser packages, refer to the recommended packages in the table below: 

| Platform | Recommended Package | Notes |
| --- | --- | --- |
| Raspbian / Debian | chromium-headless-shell | chromium or google-chrome will also work if in PATH  |
| All other Linux | chromium | devbox method installs chromium on Linux, as `chromium-headless-shell` is usually not packaged |
| macOS | Google Chrome | chromium on macOS / aarch64 is not considered stable,<br>For devbox method, Google Chrome must be installed at its default location: <br>`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`|
| Windows | Chromium or Google Chrome | devbox method installs chromium on WSL2,<br>if using native Windows (not devbox via WSL2), chromium or google-chrome should also work if in PATH | 

InkyPi will search for a Chrome-like browser in the project's PATH (if using devbox method) and your system's PATH in that order. 
