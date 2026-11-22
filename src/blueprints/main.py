from flask_babel import gettext as _
from flask import Blueprint, request, jsonify, current_app, render_template, send_file, make_response
import json
import os
from email.utils import formatdate, parsedate_to_datetime

main_bp = Blueprint("main", __name__)

@main_bp.route('/')
def main_page():
    device_config = current_app.config['DEVICE_CONFIG']
    power_manager = current_app.config.get('POWER_MANAGER')
    power_save_status = power_manager.get_status() if power_manager else {}
    return render_template('inky.html', config=device_config.get_config(), plugins=device_config.get_plugins(), power_save_status=power_save_status)

def _send_image_if_modified(image_path):
    """Send a PNG with Last-Modified, or 304 if the client's If-Modified-Since is not older."""
    if not os.path.exists(image_path):
        return jsonify({"error": _("Image not found")}), 404

    # HTTP dates have second precision
    file_mtime = int(os.path.getmtime(image_path))

    if_modified_since = request.headers.get('If-Modified-Since')
    if if_modified_since:
        try:
            if file_mtime <= int(parsedate_to_datetime(if_modified_since).timestamp()):
                return '', 304
        except (TypeError, ValueError, IndexError):
            pass

    response = send_file(image_path, mimetype='image/png')
    response.headers['Last-Modified'] = formatdate(file_mtime, usegmt=True)
    response.headers['Cache-Control'] = 'no-cache'
    return response


@main_bp.route('/api/current_image')
def get_current_image():
    """Serve current_image.png with conditional request support (If-Modified-Since)."""
    image_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'current_image.png')
    return _send_image_if_modified(image_path)


@main_bp.route('/api/display_image')
def get_display_image():
    """Panel-ready image for thin clients (server mode), with If-Modified-Since support."""
    from utils import thin_clients

    device_config = current_app.config['DEVICE_CONFIG']
    client_name = request.headers.get(thin_clients.CLIENT_HEADER)
    if client_name:
        reported = thin_clients.parse_settings_header(request.headers.get(thin_clients.SETTINGS_HEADER))
        thin_clients.record_poll(device_config, client_name, reported)

    response = make_response(_send_image_if_modified(device_config.display_image_file))
    # settings chosen in this server's web UI; the client applies them (also on 304 and 404)
    wanted = thin_clients.desired_settings(device_config)
    if wanted:
        response.headers[thin_clients.SETTINGS_HEADER] = json.dumps(wanted)
    return response


@main_bp.route('/api/plugin_order', methods=['POST'])
def save_plugin_order():
    """Save the custom plugin order."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    order = data.get('order', [])

    if not isinstance(order, list):
        return jsonify({"error": _("Order must be a list")}), 400

    device_config.set_plugin_order(order)

    return jsonify({"success": True})