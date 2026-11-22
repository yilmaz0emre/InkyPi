import logging
import os

from display.abstract_display import AbstractDisplay

logger = logging.getLogger(__name__)


class ServerDisplay(AbstractDisplay):
    """No panel attached: InkyPi runs as a render server for thin clients.

    The image arrives already processed by DisplayManager (orientation, resize, inversion,
    enhancement), so it is exactly what the client's panel should show. It is kept in a single
    file (no history, so a long-running server does not fill its disk) and served at
    /api/display_image.
    """

    def initialize_display(self):
        logger.info(f"Server display: panel images are written to {self.device_config.display_image_file}")

    def display_image(self, image, image_settings=[]):
        if not image:
            raise ValueError("No image provided.")
        path = self.device_config.display_image_file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # write atomically so a client never downloads a half-written PNG
        tmp_path = path + ".tmp"
        image.save(tmp_path, "PNG")
        os.replace(tmp_path, path)
        logger.info(f"Panel image updated for thin clients: {path}")
