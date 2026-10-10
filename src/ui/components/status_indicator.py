import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, cairo, GLib
import math
from enum import Enum, auto
from collections import namedtuple

StatusIndicatorValues = namedtuple("StatusIndicatorValues", ["state", "blinking"])

class StatusIndicatorState(Enum):
    """States of items shown with color of indicator, from least to most important (item with many states shows the
    most important one). Item that is actively used (eg. building, running operation) blinks. Described in README."""
    IDLE = auto()     # Gray: not used, not mounted, no changes.
    LOADED = auto()   # Blue: mounted, loaded or running.
    CHANGED = auto()  # Purple: has changes (eg. not saved changes of Git directory).
    WARNING = auto()  # Orange: has warnings.
    ERROR = auto()    # Red: has errors.

    def color(self) -> tuple[float, float, float]:
        """Colors of GNOME palette (gray is light 5, others are 3), the same in light and dark style."""
        match self:
            case StatusIndicatorState.IDLE: return (0x9a / 255, 0x99 / 255, 0x96 / 255)
            case StatusIndicatorState.LOADED: return (0x35 / 255, 0x84 / 255, 0xe4 / 255)
            case StatusIndicatorState.CHANGED: return (0x91 / 255, 0x41 / 255, 0xac / 255)
            case StatusIndicatorState.WARNING: return (0xff / 255, 0x78 / 255, 0x00 / 255)
            case StatusIndicatorState.ERROR: return (0xe0 / 255, 0x1b / 255, 0x24 / 255)

def status_values(state: StatusIndicatorState, blinking: bool = False) -> StatusIndicatorValues:
    """Indicator values. Actively used item is at least loaded, so blinking is visible."""
    if blinking and state == StatusIndicatorState.IDLE:
        state = StatusIndicatorState.LOADED
    return StatusIndicatorValues(state=state, blinking=blinking)

def most_important_state(*states: StatusIndicatorState) -> StatusIndicatorState:
    return max(states, key=lambda state: state.value)

class StatusIndicator(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        # Set fixed content size
        self.set_content_width(10)
        self.set_content_height(10)
        # State
        self._state = StatusIndicatorState.IDLE
        self._blinking = False
        self._tick_id = None
        self._dimmed = False
        # Use custom draw function
        self.set_draw_func(self._on_draw)

    def set_values(self, values: StatusIndicatorValues):
        values = status_values(values.state, values.blinking)
        self.set_state(values.state)
        self.set_blinking(values.blinking)

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
        red, green, blue = self._state.color()
        alpha = 0.3 if self._dimmed else 1.0
        radius = min(width, height) / 2 - 0.5
        ctx.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        ctx.set_source_rgba(red, green, blue, alpha)
        ctx.fill_preserve()
        # Edge in darker shade of the same color.
        ctx.set_source_rgba(red * 0.7, green * 0.7, blue * 0.7, alpha)
        ctx.set_line_width(1)
        ctx.stroke()


# ------------------------------------------------------------------------------
# Status of whole section (eg. in side menu): most important state of its items, blinking when any item blinks or any
# of its operations runs. None when there is nothing to show.

def combined_status(values: list[StatusIndicatorValues]) -> StatusIndicatorValues | None:
    values = [value for value in values if value is not None]
    if not values:
        return None
    state = most_important_state(*(value.state for value in values))
    blinking = any(value.blinking for value in values)
    if state == StatusIndicatorState.IDLE and not blinking:
        return None
    return status_values(state, blinking)

def items_status(items, property_name: str = "status_indicator_values") -> list[StatusIndicatorValues]:
    result = []
    for item in items:
        try:
            result.append(getattr(item, property_name))
        except Exception as e:
            print(f"Failed to read status of {item}: {e}")
    return result

def processes_status(*process_classes) -> list[StatusIndicatorValues]:
    """Running processes (installations, updates, builds) of given classes, as blinking indicators."""
    from .multistage_process import MultiStageProcess, MultiStageProcessState
    return [
        status_values(StatusIndicatorState.LOADED, blinking=True)
        for process_class in process_classes
        for process in MultiStageProcess.get_started_processes_by_class(process_class)
        if process.status == MultiStageProcessState.IN_PROGRESS
    ]
