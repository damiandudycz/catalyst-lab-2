from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, GObject, Adw
from .multistage_process import (
    # Process
    MultiStageProcess,
    MultiStageProcessState,
    MultiStageProcessEvent,
    # Stages
    MultiStageProcessStage,
    MultiStageProcessStageState,
    MultiStageProcessStageEvent
)

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/multistage_process_execution_view/multistage_process_execution_view.ui')
class MultistageProcessExecutionView(Gtk.Box):
    __gtype_name__ = 'MultistageProcessExecutionView'
    __gsignals__ = { "finish_process": (GObject.SIGNAL_RUN_FIRST, None, ()) }

    title_label = Gtk.Template.Child()
    process_steps_list = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    finish_button = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.multistage_process: MultiStageProcess | None = None

    def set_multistage_process(self, multistage_process: MultiStageProcess | None = None):
        """Call when multistage_process is started"""
        if self.multistage_process is not None:
            raise("multistage_process already set")
        if multistage_process:
            if multistage_process.status == MultiStageProcessState.SETUP:
                raise("multistage_process needs to be started before connecting")
            self.multistage_process = multistage_process
            self.title_label.set_label(multistage_process.title)
            self.progress_bar.set_fraction(multistage_process.progress)
            self._update_installation_steps(steps=multistage_process.stages)
            self._set_current_stage(multistage_process.status)
            self.bind_installation_events(multistage_process)

    def bind_installation_events(self, multistage_process: MultiStageProcess):
        multistage_process.event_bus.subscribe(
            MultiStageProcessEvent.STATE_CHANGED, self._set_current_stage
        )
        multistage_process.event_bus.subscribe(
            MultiStageProcessEvent.PROGRESS_CHANGED, self._update_progress
        )

    @Gtk.Template.Callback()
    def on_cancel_pressed(self, _):
        self.multistage_process.cancel()

    @Gtk.Template.Callback()
    def on_finish_pressed(self, _):
        self.emit("finish_process")
        self.multistage_process.clean_from_started_processes()


    def _update_progress(self, progress):
        self.progress_bar.set_fraction(self.multistage_process.progress)

    def _update_installation_steps(self, steps: list[MultiStageProcessStage]):
        if hasattr(self, "_installation_rows"):
            for row in self._installation_rows:
                self.process_steps_list.remove(row)
        self._installation_rows = []
        tools_check_buttons_group = []
        running_stage_row = None
        for step in steps:
            row = MultiStageProcessStageRow(step=step, owner=self)
            self.process_steps_list.add(row)
            self._installation_rows.append(row)
            if step.state == MultiStageProcessStageState.IN_PROGRESS:
                running_stage_row = row
        if running_stage_row:
            GLib.idle_add(self._scroll_to_installation_step_row, running_stage_row)

    def _scroll_to_installation_step_row(self, row: MultiStageProcessStageRow):
        def _scroll(widget):
            scrolled_window = self.process_steps_list.get_ancestor(Gtk.ScrolledWindow)
            vadjustment = scrolled_window.get_vadjustment()
            _, y = row.translate_coordinates(self.process_steps_list, 0, 0)
            row_height = row.get_allocated_height()
            visible_height = vadjustment.get_page_size()
            center_y = y + row_height / 2 - visible_height / 2
            max_value = vadjustment.get_upper() - vadjustment.get_page_size()
            scroll_to = max(0, min(center_y, max_value))
            vadjustment.set_value(scroll_to)
        GLib.idle_add(_scroll, row)

    def _scroll_to_installation_steps_bottom(self):
        def _scroll():
            scrolled_window = self.process_steps_list.get_ancestor(Gtk.ScrolledWindow)
            vadjustment = scrolled_window.get_vadjustment()
            bottom = vadjustment.get_upper() - vadjustment.get_page_size()
            vadjustment.set_value(bottom)
        GLib.timeout_add(100, _scroll)

    def _set_current_stage(self, stage: MultiStageProcessState):
        self.cancel_button.set_visible(stage == MultiStageProcessState.IN_PROGRESS)
        self.finish_button.set_visible(stage != MultiStageProcessState.IN_PROGRESS)
        # Add label with summary for completion states:
        def display_status(text: str, style: str | None):
            label = Gtk.Label(label=text)
            label.set_margin_top(12)
            label.set_margin_bottom(12)
            label.set_margin_start(24)
            label.set_margin_end(24)
            label.add_css_class("heading")
            if style:
                label.add_css_class(style)
            self.process_steps_list.add(label)
            self._scroll_to_installation_steps_bottom()
        match stage:
            case MultiStageProcessState.COMPLETED:
                display_status(text="Installation completed successfully.", style="success")
            case MultiStageProcessState.FAILED:
                display_status(text="Installation failed.", style="error")

class MultiStageProcessStageRow(Adw.ActionRow):

    def __init__(self, step: MultiStageProcessStage, owner: MultistageProcessExecutionView):
        super().__init__(title=step.name, subtitle=step.description)
        self.step = step
        self.owner = owner
        self.progress_label = Gtk.Label()
        self.progress_label.add_css_class("dim-label")
        self.progress_label.add_css_class("caption")
        self._update_status_label()
        self.add_suffix(self.progress_label)
        self.set_sensitive(step.state != MultiStageProcessStageState.SCHEDULED)
        self._set_status_icon(state=step.state)
        step.event_bus.subscribe(
            MultiStageProcessStageEvent.STATE_CHANGED,
            self._step_state_changed
        )
        step.event_bus.subscribe(
            MultiStageProcessStageEvent.PROGRESS_CHANGED,
            self._step_progress_changed
        )

    def _step_progress_changed(self, progress: float | None):
        self._update_status_label()

    def _step_state_changed(self, state: MultiStageProcessStageState):
        self.set_sensitive(state != MultiStageProcessStageState.SCHEDULED)
        self._set_status_icon(state=state)
        self.owner._scroll_to_installation_step_row(self)
        self._update_status_label()

    def _update_status_label(self):
        self.progress_label.set_label(
            "" if self.step.state == MultiStageProcessStageState.SCHEDULED else ("..." if self.step.progress is None else f"{int(self.step.progress * 100)}%")
        )

    def _set_status_icon(self, state: MultiStageProcessStageState):
        if not hasattr(self, "status_icon"):
            self.status_icon = Gtk.Image()
            self.status_icon.set_pixel_size(24)
            self.add_prefix(self.status_icon)
        icon_name = {
            MultiStageProcessStageState.SCHEDULED: "square-alt-arrow-right-svgrepo-com-symbolic",
            MultiStageProcessStageState.IN_PROGRESS: "menu-dots-square-svgrepo-com-symbolic",
            MultiStageProcessStageState.FAILED: "error-box-svgrepo-com-symbolic",
            MultiStageProcessStageState.COMPLETED: "check-square-svgrepo-com-symbolic"
        }.get(state)
        styles = {
            MultiStageProcessStageState.SCHEDULED: "dimmed",
            MultiStageProcessStageState.IN_PROGRESS: "",
            MultiStageProcessStageState.FAILED: "error",
            MultiStageProcessStageState.COMPLETED: "success"
        }
        style = styles.get(state)
        self.status_icon.set_from_icon_name(icon_name)
        for css_class in styles.values():
            if css_class:
                self.status_icon.remove_css_class(css_class)
        if style:
            self.status_icon.add_css_class(style)

