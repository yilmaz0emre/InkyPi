from flask_babel import gettext as _, format_timedelta
from flask import Blueprint, request, jsonify, current_app, render_template, Response
from utils.time_utils import calculate_seconds
from utils.power_manager import MIN_AWAKE_SECONDS
from datetime import datetime, timedelta
import os
import pytz
import logging
import io

# Try to import cysystemd for journal reading (Linux only)
try:
    from cysystemd.reader import JournalReader, JournalOpenMode, Rule
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False
    # Define dummy classes for when cysystemd is not available
    class JournalOpenMode:
        SYSTEM = None
    class Rule:
        pass
    class JournalReader:
        def __init__(self, *args, **kwargs):
            pass


logger = logging.getLogger(__name__)
settings_bp = Blueprint("settings", __name__)

# what a thin client runs with until the server's web UI says otherwise (see thin_client.DEFAULTS)
THIN_CLIENT_DEFAULTS = {"power_save": False, "poll_interval": 300, "offline_after": 3600}


def _is_server(device_config):
    return device_config.get_config("display_type") == "server"


def _thin_client_context(device_config):
    """Display devices of a render server and the settings shown in its form."""
    from utils import thin_clients

    clients = thin_clients.client_overview(device_config)
    for client in clients:
        client["last_seen_text"] = format_timedelta(timedelta(seconds=-client["seconds_ago"]), add_direction=True)
        if client["poll_interval"]:
            client["poll_text"] = format_timedelta(timedelta(seconds=client["poll_interval"]))
        if client["next_check_in"] is not None:
            client["next_check_text"] = format_timedelta(timedelta(seconds=client["next_check_in"]), add_direction=True)
    # the form starts from the chosen settings, else from what the newest client reports
    settings = dict(THIN_CLIENT_DEFAULTS)
    if clients:
        settings.update({k: v for k, v in clients[0].items() if k in THIN_CLIENT_DEFAULTS and v is not None})
    settings.update(thin_clients.desired_settings(device_config))
    return clients, settings

def _thin_client_settings_from_form(form_data):
    """Settings for the display devices from the server's form; returns (settings, error)."""
    try:
        poll_minutes = int(form_data.get("tc_poll_minutes", ""))
        offline_hours = int(form_data.get("tc_offline_hours", ""))
    except ValueError:
        return None, _("Enter whole numbers for the check interval and the offline badge.")
    if not 1 <= poll_minutes <= 1440:
        return None, _("The check interval must be between 1 and 1440 minutes.")
    if not 0 <= offline_hours <= 168:
        return None, _("The offline badge delay must be between 0 and 168 hours.")
    return {
        "power_save": form_data.get("tc_power_save") == "on",
        "poll_interval": poll_minutes * 60,
        "offline_after": offline_hours * 3600,
    }, None


@settings_bp.route('/settings')
def settings_page():
    device_config = current_app.config['DEVICE_CONFIG']
    power_manager = current_app.config.get('POWER_MANAGER')
    power_save_status = power_manager.get_status() if power_manager else {}
    timezones = sorted(pytz.all_timezones_set)
    is_server = _is_server(device_config)
    clients, client_settings = _thin_client_context(device_config) if is_server else ([], {})
    return render_template('settings.html', device_settings=device_config.get_config(), timezones=timezones,
                           power_save_status=power_save_status, min_awake_seconds=MIN_AWAKE_SECONDS,
                           is_server=is_server, thin_clients=clients, thin_client_settings=client_settings)

@settings_bp.route('/save_settings', methods=['POST'])
def save_settings():
    device_config = current_app.config['DEVICE_CONFIG']

    try:
        form_data = request.form.to_dict()

        unit, interval, time_format = form_data.get('unit'), form_data.get("interval"), form_data.get("timeFormat")
        if not unit or unit not in ["minute", "hour"]:
            return jsonify({"error": _("Plugin cycle interval unit is required")}), 400
        if not interval or not interval.isnumeric():
            return jsonify({"error": _("Refresh interval is required")}), 400
        if not form_data.get("timezoneName"):
            return jsonify({"error": _("Time Zone is required")}), 400
        if not time_format or time_format not in ["12h", "24h"]:
            return jsonify({"error": _("Time format is required")}), 400
        previous_interval_seconds = device_config.get_config("plugin_cycle_interval_seconds")
        plugin_cycle_interval_seconds = calculate_seconds(int(interval), unit)
        if plugin_cycle_interval_seconds > 86400 or plugin_cycle_interval_seconds <= 0:
            return jsonify({"error": _("Plugin cycle interval must be less than 24 hours")}), 400

        from utils.i18n import normalize_language
        language = normalize_language(form_data.get("language", "en"))

        settings = {
            "name": form_data.get("deviceName"),
            "language": language,
            "orientation": form_data.get("orientation"),
            "inverted_image": form_data.get("invertImage"),
            "log_system_stats": form_data.get("logSystemStats"),
            "timezone": form_data.get("timezoneName"),
            "time_format": form_data.get("timeFormat"),
            "plugin_cycle_interval_seconds": plugin_cycle_interval_seconds,
            "image_settings": {
                "saturation": float(form_data.get("saturation", "1.0")),
                "brightness": float(form_data.get("brightness", "1.0")),
                "sharpness": float(form_data.get("sharpness", "1.0")),
                "contrast": float(form_data.get("contrast", "1.0"))
            }
        }
        if "inky_saturation" in form_data:
            settings["image_settings"]["inky_saturation"] = float(form_data.get("inky_saturation", "0.5"))

        power_manager = current_app.config.get('POWER_MANAGER')
        previous_ps_config = power_manager.get_config() if power_manager else None
        if _is_server(device_config):
            # a render server manages the power save of its display devices, not its own
            client_settings, error = _thin_client_settings_from_form(form_data)
            if error:
                return jsonify({"error": error}), 400
            settings["thin_client_settings"] = client_settings
        else:
            # Power save configuration
            try:
                ps_awake_duration = int(form_data.get("ps_awake_duration", 3))
            except (ValueError, TypeError):
                ps_awake_duration = 3
            ps_awake_unit = form_data.get("ps_awake_unit", "minute")

            try:
                ps_sleep_duration = int(form_data.get("ps_sleep_duration", 1))
            except (ValueError, TypeError):
                ps_sleep_duration = 1
            ps_sleep_unit = form_data.get("ps_sleep_unit", "hour")

            if ps_awake_unit not in ["second", "minute"]:
                ps_awake_unit = "minute"
            # Store what is actually used, so the form never shows a value below the enforced minimum
            min_awake = MIN_AWAKE_SECONDS if ps_awake_unit == "second" else 1
            ps_config = {
                "awake_duration": max(min_awake, ps_awake_duration),
                "awake_unit": ps_awake_unit,
                "sleep_duration": max(1, ps_sleep_duration),
                "sleep_unit": ps_sleep_unit if ps_sleep_unit in ["minute", "hour"] else "hour",
                "turn_off_wifi": form_data.get("ps_turn_off_wifi") == "on",
                "turn_off_bluetooth": form_data.get("ps_turn_off_bluetooth") == "on",
                "turn_off_hdmi": form_data.get("ps_turn_off_hdmi") == "on",
                "turn_off_led": form_data.get("ps_turn_off_led") == "on",
            }
            settings["power_save_config"] = ps_config

        device_config.update_config(settings)

        # Only wake the background thread when its schedule changed; any signal cuts a
        # power save sleep short (Wi-Fi can stay on during sleep)
        if plugin_cycle_interval_seconds != previous_interval_seconds or (
                power_manager and power_manager.get_config() != previous_ps_config):
            refresh_task = current_app.config['REFRESH_TASK']
            refresh_task.signal_config_change()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": _("An error occurred: %(error)s", error=str(e))}), 500
    return jsonify({"success": True, "message": _("Saved settings.")})

@settings_bp.route('/power_save/toggle', methods=['POST'])
def power_save_toggle():
    power_manager = current_app.config.get('POWER_MANAGER')
    refresh_task = current_app.config.get('REFRESH_TASK')
    if not power_manager:
        return jsonify({"error": _("Power manager not available")}), 500

    data = request.get_json(silent=True) or {}
    if "active" in data:
        active = _parse_bool(data["active"])
        if active is None:
            return jsonify({"error": _("Invalid value for 'active'")}), 400
    else:
        active = not power_manager.is_active
    power_manager.set_active(active)
    if refresh_task:
        refresh_task.signal_config_change()

    msg = _("Power save mode activated.") if power_manager.is_active else _("Power save mode deactivated.")
    return jsonify({"success": True, "active": power_manager.is_active, "message": msg, "status": power_manager.get_status()})

KEEP_AWAKE_MIN_SECONDS = 60
KEEP_AWAKE_MAX_SECONDS = 3600

@settings_bp.route('/power_save/keep_awake', methods=['POST'])
def power_save_keep_awake():
    power_manager = current_app.config.get('POWER_MANAGER')
    if not power_manager:
        return jsonify({"error": _("Power manager not available")}), 500

    data = request.get_json(silent=True) or {}
    try:
        seconds = int(data.get("seconds", 300))
    except (ValueError, TypeError):
        return jsonify({"error": _("Invalid keep-awake duration")}), 400
    seconds = min(max(seconds, KEEP_AWAKE_MIN_SECONDS), KEEP_AWAKE_MAX_SECONDS)
    power_manager.request_keep_awake(seconds)
    return jsonify({"success": True, "message": _("Extended awake duration by %(minutes)d minutes.", minutes=seconds//60), "status": power_manager.get_status()})

def _parse_bool(value):
    """Parse a JSON boolean, tolerating string/int forms; returns None if not recognizable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "on", "yes"):
            return True
        if normalized in ("false", "0", "off", "no"):
            return False
    return None

@settings_bp.route('/power_save/status')
def power_save_status():
    power_manager = current_app.config.get('POWER_MANAGER')
    if not power_manager:
        return jsonify({"is_active": False})
    return jsonify(power_manager.get_status())

@settings_bp.route('/shutdown', methods=['POST'])
def shutdown():
    data = request.get_json() or {}
    if data.get("reboot"):
        logger.info("Reboot requested")
        os.system("sudo reboot")
    else:
        logger.info("Shutdown requested")
        os.system("sudo shutdown -h now")
    return jsonify({"success": True})

@settings_bp.route('/download-logs')
def download_logs():
    try:
        buffer = io.StringIO()
        
        # Get 'hours' from query parameters, default to 2 if not provided or invalid
        hours_str = request.args.get('hours', '2')
        try:
            hours = int(hours_str)
        except ValueError:
            hours = 2
        since = datetime.now() - timedelta(hours=hours)

        if not JOURNAL_AVAILABLE:
            # Return a message when running in development mode without systemd
            buffer.write(f"Log download not available in development mode (cysystemd not installed).\n")
            buffer.write(f"Logs would normally show InkyPi service logs from the last {hours} hours.\n")
            buffer.write(f"\nTo see Flask development logs, check your terminal output.\n")
        else:
            reader = JournalReader()
            reader.open(JournalOpenMode.SYSTEM)
            reader.add_filter(Rule("_SYSTEMD_UNIT", "inkypi.service"))
            reader.seek_realtime_usec(int(since.timestamp() * 1_000_000))

            for record in reader:
                try:
                    ts = datetime.fromtimestamp(record.get_realtime_usec() / 1_000_000)
                    formatted_ts = ts.strftime("%b %d %H:%M:%S")
                except Exception:
                    formatted_ts = "??? ?? ??:??:??"

                data = record.data
                hostname = data.get("_HOSTNAME", "unknown-host")
                identifier = data.get("SYSLOG_IDENTIFIER") or data.get("_COMM", "?")
                pid = data.get("_PID", "?")
                msg = data.get("MESSAGE", "").rstrip()

                # Format the log entry similar to the journalctl default output
                buffer.write(f"{formatted_ts} {hostname} {identifier}[{pid}]: {msg}\n")

        buffer.seek(0)
        # Add date and time to the filename
        now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"inkypi_{now_str}.log"
        return Response(
            buffer.read(),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return Response(f"Error reading logs: {e}", status=500, mimetype="text/plain")

