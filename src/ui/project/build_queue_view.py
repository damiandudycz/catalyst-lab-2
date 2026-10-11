from __future__ import annotations
from dataclasses import dataclass
from gi.repository import Gtk, Adw, GLib
from .app_events import app_event_bus, AppEvents
from .build_queue import BuildQueue, BuildQueueEvent, QueuedBuild, running_build, LATEST

# ------------------------------------------------------------------------------
# Build queue: page with queued builds (order, removing, starting or pausing queue), and state of build buttons of
# projects (create build, add it to queue, or show queue).

@dataclass
class BuildButtonState:
    visible: bool
    sensitive: bool
    title: str
    tooltip: str | None
    opens_queue: bool # Button shows queue (project is queued), otherwise it creates build (or adds it to queue).

def build_button_state(project_directory) -> BuildButtonState:
    """State of button creating build of project (hidden while project is built, its progress is shown instead)."""
    running = running_build()
    queue = BuildQueue.shared()
    if running is not None and running.project_directory.id == project_directory.id:
        return BuildButtonState(False, False, "Create build", None, False)
    if queue.entry_for(project_directory):
        return BuildButtonState(True, True, "Queued, show build queue", None, True)
    if running is not None:
        error = project_directory.configuration_error
        return BuildButtonState(True, error is None, GLib.markup_escape_text(error) if error else "Add build to queue",
                                error or f"Starts after build of {running.project_directory.name} and queued builds", False)
    toolset = project_directory.get_toolset()
    reason = toolset.busy_reason if toolset else "Select toolset in project configuration"
    return BuildButtonState(True, reason is None, GLib.markup_escape_text(reason) if reason else "Create build", reason, False)

def present_build_queue():
    app_event_bus.emit(AppEvents.PRESENT_VIEW, BuildQueueView(), "Build queue", 640, 480)

class BuildQueueView(Gtk.Box):
    """Queued builds in order they start, they can be moved and removed. Queue can be paused and started."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._window = None
        self.queue = BuildQueue.shared()
        scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24,
                               margin_top=12, margin_bottom=24)
        scrolled_window.set_child(self.content)
        self.append(scrolled_window)
        # Bottom bar like in wizards: starting or pausing queue.
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom_bar.append(Gtk.Separator())
        buttons = Gtk.CenterBox(margin_start=24, margin_end=24, margin_top=12, margin_bottom=12)
        self.queue_button = Gtk.Button()
        self.queue_button.connect("clicked", self._on_queue_button_clicked)
        buttons.set_end_widget(self.queue_button)
        bottom_bar.append(buttons)
        self.append(bottom_bar)
        self.groups: list[Gtk.Widget] = []
        self.queue.event_bus.subscribe(BuildQueueEvent.CHANGED, self._on_queue_changed)
        self._show()

    def _on_queue_changed(self, *args):
        self._show()

    def _show(self):
        for group in self.groups:
            self.content.remove(group)
        self.groups = []
        running = running_build()
        if running is not None:
            group = Adw.PreferencesGroup(title="Building now")
            row = Adw.ActionRow(title=GLib.markup_escape_text(running.project_directory.name),
                                subtitle=GLib.markup_escape_text(", ".join(stage.name for stage in running.plan.build_order())))
            row.add_prefix(Gtk.Image(icon_name="menu-dots-square-svgrepo-com-symbolic", pixel_size=24))
            group.add(row)
            self._add(group)
        entries = list(self.queue.entries)
        if self.queue.paused and entries:
            description = "Queue is paused, builds start after it's started."
            if self.queue.pause_reason:
                description = f"Paused: {self.queue.pause_reason}. Builds start after queue is started."
        elif running is not None:
            description = "Builds start one after another when previous build finishes."
        else:
            description = None
        group = Adw.PreferencesGroup(title="Queued builds", description=description)
        if not entries:
            group.add(Adw.ActionRow(title="No builds are queued",
                                    subtitle="While a project is built, builds of other projects can be added to queue."))
        for index, entry in enumerate(entries):
            group.add(self._entry_row(entry, index, len(entries)))
        self._add(group)
        self.queue_button.set_visible(bool(entries))
        self.queue_button.set_label("Start queue" if self.queue.paused else "Pause queue")
        for style in ("suggested-action",):
            self.queue_button.remove_css_class(style)
        if self.queue.paused:
            self.queue_button.add_css_class("suggested-action")

    def _add(self, widget: Gtk.Widget):
        self.content.append(widget)
        self.groups.append(widget)

    def _entry_row(self, entry: QueuedBuild, index: int, count: int) -> Adw.ActionRow:
        project = entry.project_directory
        stages = entry.stage_names
        snapshot = "latest snapshot" if entry.snapshot == LATEST else (entry.snapshot or "snapshot of project")
        subtitle = f"{', '.join(stages) if stages else 'Stages were removed'}\n{snapshot} · queued {entry.queued.strftime('%Y-%m-%d %H:%M')}"
        row = Adw.ActionRow(title=GLib.markup_escape_text(project.name if project else "Removed project"),
                            subtitle=GLib.markup_escape_text(subtitle))
        row.set_subtitle_lines(3)
        icon = Gtk.Image(icon_name="clock-square-svgrepo-com-symbolic", pixel_size=24)
        icon.add_css_class("dimmed")
        row.add_prefix(icon)
        for icon_name, tooltip, offset, sensitive in (("go-up-symbolic", "Move up", -1, index > 0),
                                                       ("go-down-symbolic", "Move down", 1, index < count - 1)):
            button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER, sensitive=sensitive)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, offset=offset: self.queue.move(entry, offset))
            row.add_suffix(button)
        remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove from queue", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.connect("clicked", lambda _: self.queue.remove(entry))
        row.add_suffix(remove)
        return row

    def _on_queue_button_clicked(self, button):
        if self.queue.paused:
            self.queue.start()
        else:
            self.queue.pause()
