"""Shared rule for plugins that offer a compact layout for small screens / dashboard widgets.

Plugins expose a "layout style" setting with the values below; "auto" switches to the compact
layout when the render size is below the thresholds (e.g. a narrow or short dashboard widget).
"""

LAYOUT_STYLES = ("auto", "full", "short")
COMPACT_MAX_WIDTH = 450
COMPACT_MAX_HEIGHT = 260


def use_compact_layout(style, dimensions):
    """True for "short", False for "full", size-based for "auto" (and unknown values)."""
    if style == "short":
        return True
    if style == "full":
        return False
    width, height = dimensions
    return width < COMPACT_MAX_WIDTH or height < COMPACT_MAX_HEIGHT
