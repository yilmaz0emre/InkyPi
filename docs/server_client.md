# Render server + thin client

Rendering with headless Chromium is the slowest and most memory-hungry part of InkyPi on a Pi Zero 2 W. In this setup a stronger machine on the home network (e.g. a Raspberry Pi 5) does all the work, and the Pi Zero with the e-paper panel only downloads the finished image and shows it.

```
 Raspberry Pi 5 (always on)                 Pi Zero 2 W (battery, e-paper)
 ┌───────────────────────────────┐          ┌───────────────────────────────┐
 │ InkyPi in server mode          │  Wi-Fi   │ InkyPi thin client             │
 │ • web UI (home network only)   │ ◄─────── │ • wake → ask for the image     │
 │ • data fetching + Chromium     │   PNG    │ • changed? → refresh the panel │
 │ • GET /api/display_image       │ ───────► │ • sleep (optional power save)  │
 └───────────────────────────────┘          └───────────────────────────────┘
```

Nothing is exposed to the internet; both devices only need to be on the same network.

## Server (Raspberry Pi 5)

```bash
git clone https://github.com/yilmaz0emre/InkyPi.git && cd InkyPi
sudo bash install/install.sh -S -R 800x480
```

- `-S` installs InkyPi without a panel (`display_type: "server"`). SPI/I2C are left untouched.
- `-R` must match the **client's panel** resolution (800x480 for the Waveshare 7.3" ACeP, `epd7in3f`).
- The service may use up to 1 GB RAM (Chromium needs more than the default 200 MB).
- Configure everything (plugins, playlists, dashboards, orientation, image settings) in the server's web UI: `http://<server-hostname>.local`.
- The panel-ready image (already rotated, resized and enhanced) is served at `GET /api/display_image`, with `Last-Modified` / `If-Modified-Since` support.

## Thin client (Pi Zero 2 W with the panel)

Use Raspberry Pi OS Lite (64-bit), then:

```bash
git clone https://github.com/yilmaz0emre/InkyPi.git && cd InkyPi
sudo bash install/install.sh -W epd7in3f -C http://<server-hostname>.local
```

- `-W` installs the Waveshare driver for the panel (omit it for a Pimoroni Inky).
- `-C` sets the server and runs the service as `inkypi client`. No web UI and no Chromium are installed.

Optional settings in `src/config/device.json` on the client (`thin_client` section):

| Key | Default | Meaning |
| --- | --- | --- |
| `server_url` | — | Set by `-C` |
| `poll_interval` | `300` | Seconds between checks (min. 30) |
| `offline_after` | `3600` | Seconds without contact before an "offline" badge is drawn (`0` = never) |
| `power_save` | `false` | Turn Wi-Fi, Bluetooth, LEDs, ... off between checks (`power_save_config` controls what) |
| `boot_awake` | `600` | After a boot, stay fully awake this long before power saving (keeps SSH reachable) |

Restart after changes: `sudo systemctl restart inkypi`. Logs: `journalctl -u inkypi -f`.

## Behaviour

- **Unchanged image → no panel refresh.** The client sends `If-Modified-Since`; a `304` answer costs a few bytes instead of a ~30 s e-ink refresh. The last `Last-Modified` is stored on disk, so a reboot does not cause a refresh either.
- **Server or network down → last image stays** (e-paper keeps it without power). After `offline_after` a small "Offline · last update HH:MM" badge is drawn once; it is removed as soon as the server answers again.
- **Power cut at home:** router and server go down too, so the client simply keeps the last image until they are back. The Pi 5 boots automatically when power returns and the InkyPi service starts with it.
- **Wrong size:** if the server resolution does not match the panel the image is resized (and a warning is logged); fix the server's `-R`.

## Updating

Code changes reach a device only when it is updated (settings and content live in each device's own `device.json` and are not affected):

```bash
cd ~/InkyPi && git pull && sudo bash install/update.sh
```

The server/client mode is kept (it lives in the systemd drop-in, not in `inkypi.service`); on a thin client Chromium is not installed. The client rarely needs updates — only when `thin_client.py` or the panel driver changes.

Switching a device back to the normal (all-in-one) mode: run `install.sh` again without `-S`/`-C`; the mode-specific service settings are removed.
