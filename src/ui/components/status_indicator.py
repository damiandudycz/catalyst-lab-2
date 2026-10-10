import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, cairo, GLib
import math
from enum import Enum, auto
from collections import namedtuple

# Description explains state (shown when indicator is hovered), eg. "Not saved changes".
StatusIndicatorValues = namedtuple("StatusIndicatorValues", ["state", "blinking", "description"], defaults=(None,))

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

def status_values(state: StatusIndicatorState, blinking: bool = False, description: str | list[str] | None = None) -> StatusIndicatorValues:
    """Indicator values. Actively used item is at least loaded, so blinking is visible. Description can be list of
    reasons of state, shown in separate lines."""
    if blinking and state == StatusIndicatorState.IDLE:
        state = StatusIndicatorState.LOADED
    if isinstance(description, list):
        description = "\n".join(line for line in description if line) or None
    return StatusIndicatorValues(state=state, blinking=blinking, description=description)

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
        values = status_values(values.state, values.blinking, values.description)
        self.set_state(values.state)
        self.set_blinking(values.blinking)
        self.set_tooltip_text(values.description)

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
# Status of whole section (eg. in side menu): statuses of its items and running operations, with keys identifying them
# (so side menu can hide states that were already seen). Combined status is the most important state of items, blinking
# when any item blinks or any of its operations runs.

StatusEntry = namedtuple("StatusEntry", ["key", "values", "label"], defaults=(None,)) # Label is name of item.

def combined_status(entries: list) -> StatusIndicatorValues | None:
    """Most important state of entries (StatusEntry or StatusIndicatorValues), None when there is nothing to show."""
    values = [entry.values if isinstance(entry, StatusEntry) else entry for entry in entries]
    values = [value for value in values if value is not None]
    if not values:
        return None
    state = most_important_state(*(value.state for value in values))
    blinking = any(value.blinking for value in values)
    if state == StatusIndicatorState.IDLE and not blinking:
        return None
    descriptions = [entry_description(entry) for entry in entries
                    if (entry.values if isinstance(entry, StatusEntry) else entry) is not None]
    return status_values(state, blinking, [description for description in descriptions if description])

def entry_description(entry) -> str | None:
    """Description of entry, with name of its item (eg. "Raspberry Pi 5: Not saved changes")."""
    values = entry.values if isinstance(entry, StatusEntry) else entry
    if values is None or (values.state == StatusIndicatorState.IDLE and not values.blinking) or not values.description:
        return None
    label = entry.label if isinstance(entry, StatusEntry) else None
    description = values.description.replace("\n", ", ")
    return f"{label}: {description}" if label else description

def items_status(items, property_name: str = "status_indicator_values") -> list[StatusEntry]:
    result = []
    for item in items:
        try:
            key = (property_name, getattr(item, "id", None) or getattr(item, "uuid", None) or id(item))
            result.append(StatusEntry(key=key, values=getattr(item, property_name), label=getattr(item, "name", None)))
        except Exception as e:
            print(f"Failed to read status of {item}: {e}")
    return result

def processes_status(*process_classes) -> list[StatusEntry]:
    """Running processes (installations, updates, builds) of given classes, as blinking indicators."""
    from .multistage_process import MultiStageProcess, MultiStageProcessState
    return [
        StatusEntry(key=("process", id(process)), label=process.name(),
                    values=status_values(StatusIndicatorState.LOADED, blinking=True, description=process.title))
        for process_class in process_classes
        for process in MultiStageProcess.get_started_processes_by_class(process_class)
        if process.status == MultiStageProcessState.IN_PROGRESS
    ]

# Warnings and errors (eg. failed build) are reported in side menu until their section is opened.
_HIDDEN_WHEN_SEEN = {StatusIndicatorState.WARNING, StatusIndicatorState.ERROR}

def unseen_status_entries(entries: list[StatusEntry], seen: set, section_open: bool) -> tuple[list[StatusEntry], set]:
    """Entries of section shown in side menu, and updated seen states. Warnings and errors are shown until section is
    opened (they are seen while it's open). State that disappears and comes back (eg. failure of next build) is shown
    again. Other states (changes, loaded, running operations) are always shown."""
    current = {(entry.key, entry.values.state) for entry in entries
               if entry.values is not None and entry.values.state in _HIDDEN_WHEN_SEEN}
    seen = current if section_open else seen & current
    unseen = [entry for entry in entries if entry.values is not None
              and (entry.values.blinking or (entry.key, entry.values.state) not in seen)]
    return unseen, seen
