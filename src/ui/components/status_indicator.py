import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, cairo, GLib, Adw
import math
from enum import Enum, auto

class StatusIndicatorState(Enum):
    DISABLED = auto()
    ENABLED = auto()
    ENABLED_UNSAFE = auto()

    def color_name(self) -> str:
        match self:
            case StatusIndicatorState.DISABLED:
                return "shade_color"
            case StatusIndicatorState.ENABLED:
                return "accent_bg_color"
            case StatusIndicatorState.ENABLED_UNSAFE:
                return "warning_bg_color"

class StatusIndicator(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        # Set fixed content size
        self.set_content_width(10)
        self.set_content_height(10)
        # State
        self._state = StatusIndicatorState.DISABLED
        self._blinking = False
        self._tick_id = None
        self._dimmed = False
        # Use custom draw function
        self.set_draw_func(self._on_draw)

    def set_state(self, state: StatusIndicatorState):
        self._state = state
        self.queue_draw()

    def set_blinking(self, blinking: bool):
        """Enable or disable blinking animation."""
        if blinking == self._blinking:
            return
        self._blinking = blinking
        if blinking:
            self._tick_id = GLib.timeout_add(500, self._tick)
        else:
            if self._tick_id:
                GLib.source_remove(self._tick_id)
                self._tick_id = None
            self._dimmed = False
            self.queue_draw()

    def _tick(self):
        if not self._blinking:
            return False
        self._dimmed = not self._dimmed
        self.queue_draw()
        return True

    def _on_draw(self, area, ctx: cairo.Context, width, height):
        # Obtain and convert the colors
        style_context = self.get_style_context()
        _, fill_color = style_context.lookup_color(self._state.color_name())
        _, border_color = style_context.lookup_color("view_fg_color")
        # Fill
        radius = min(width, height) / 2 - 0.5
        fill_alpha = 0.3 if self._dimmed else 1.0
        ctx.set_source_rgba(fill_color.red, fill_color.green, fill_color.blue, fill_color.alpha * fill_alpha)
        ctx.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        ctx.fill_preserve()
        # Border
        ctx.set_source_rgba(border_color.red, border_color.green, border_color.blue, border_color.alpha)
        ctx.set_line_width(1)
        ctx.stroke()


