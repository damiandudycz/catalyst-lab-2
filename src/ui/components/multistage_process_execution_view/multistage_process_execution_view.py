from __future__ import annotations
from gi.repository import Gtk, GLib, Adw
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

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/components/multistage_process_execution_view/multistage_process_execution_view.ui')
class MultistageProcessExecutionView(Gtk.Box):
    __gtype_name__ = 'MultistageProcessExecutionView'

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
            self.process_steps_list.set_title(title=multistage_process.title)
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
        self.multistage_process.clean_from_started_processes()
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()

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
    """Displays stage state. Can be activated to open output of commands executed by stage."""

    def __init__(self, step: MultiStageProcessStage, owner: MultistageProcessExecutionView):
        super().__init__(title=step.name, subtitle=step.description)
        self.step = step
        self.owner = owner
        self.progress_label = Gtk.Label()
        self.progress_label.add_css_class("dim-label")
        self.progress_label.add_css_class("caption")
        self._update_status_label()
        self.add_suffix(self.progress_label)
        self.output_arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        self.add_suffix(self.output_arrow)
        self.connect("activated", self._on_activated)
        self._update_output_available()
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
        step.event_bus.subscribe(
            MultiStageProcessStageEvent.OUTPUT_LINE_ADDED,
            self._step_output_line_added
        )

    def _update_output_available(self):
        """Allow opening output only when there is some output to show."""
        has_output = bool(self.step.output_lines)
        self.set_activatable(has_output)
        self.output_arrow.set_visible(has_output)

    def _on_activated(self, row):
        navigation_view = getattr(self.owner, "content_navigation_view", None) or self.get_ancestor(Adw.NavigationView)
        if navigation_view is None:
            print("Warning: No navigation view to show step output in.")
            return
        navigation_view.push_view(MultiStageProcessStageOutputView(step=self.step), title=self.step.name)

    def _step_output_line_added(self, line: str):
        if not self.get_activatable():
            self._update_output_available()

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

class MultiStageProcessStageOutputView(Gtk.Box):
    """Displays output of commands executed by stage. Pushed when stage row is activated."""

    def __init__(self, step: MultiStageProcessStage):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.step = step
        self.output_view = Gtk.TextView()
        self.output_view.set_editable(False)
        self.output_view.set_cursor_visible(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.output_view.set_top_margin(12)
        self.output_view.set_bottom_margin(12)
        self.output_view.set_left_margin(12)
        self.output_view.set_right_margin(12)
        self.output_view.add_css_class("transparent-bg")
        self.output_buffer = self.output_view.get_buffer()
        self.output_buffer.set_text("\n".join(self.step.output_lines))
        self.output_scrolled_window = Gtk.ScrolledWindow()
        self.output_scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.output_scrolled_window.set_hexpand(True)
        self.output_scrolled_window.set_vexpand(True)
        self.output_scrolled_window.set_child(self.output_view)
        self.append(self.output_scrolled_window)
        # Keep output scrolled to the bottom while new lines arrive. Stop following when user
        # scrolls up, resume when user scrolls back to the bottom.
        # Only user input changes following, as GtkTextView also moves scroll position by
        # itself while measuring lines.
        self._follow_output = True
        self._user_scroll_time = 0
        adjustment = self.output_scrolled_window.get_vadjustment()
        adjustment.connect("value-changed", self._on_output_scrolled)
        adjustment.connect("changed", self._on_output_size_changed)
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.KINETIC
        )
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect("scroll", lambda *args: self._mark_user_scroll() or False)
        scroll_controller.connect("decelerate", lambda *args: self._mark_user_scroll())
        self.output_scrolled_window.add_controller(scroll_controller)
        scrollbar_drag = Gtk.GestureDrag()
        scrollbar_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scrollbar_drag.connect("drag-begin", lambda *args: self._mark_user_scroll())
        scrollbar_drag.connect("drag-update", lambda *args: self._mark_user_scroll())
        self.output_scrolled_window.get_vscrollbar().add_controller(scrollbar_drag)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", lambda *args: self._mark_user_scroll() or False)
        self.output_view.add_controller(key_controller)
        # Show the latest output when view is shown. Text is laid out after it's mapped, so scroll again once it's measured.
        self.output_view.connect("map", lambda *args: GLib.idle_add(lambda: self._scroll_output_to_end() and False))
        step.event_bus.subscribe(
            MultiStageProcessStageEvent.OUTPUT_LINE_ADDED,
            self._step_output_line_added
        )

    def _step_output_line_added(self, line: str):
        end_iter = self.output_buffer.get_end_iter()
        self.output_buffer.insert(end_iter, line if self.output_buffer.get_char_count() == 0 else "\n" + line)

    def _is_output_at_bottom(self) -> bool:
        adjustment = self.output_scrolled_window.get_vadjustment()
        return adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 1

    def _mark_user_scroll(self):
        self._user_scroll_time = GLib.get_monotonic_time()

    def _on_output_scrolled(self, adjustment: Gtk.Adjustment):
        # Scroll position changes within a second after user input are made by user (including kinetic scrolling).
        if GLib.get_monotonic_time() - self._user_scroll_time < 1_000_000:
            self._follow_output = self._is_output_at_bottom()
        elif self._follow_output and not self._is_output_at_bottom():
            self._scroll_output_to_end()

    def _on_output_size_changed(self, adjustment: Gtk.Adjustment):
        # Content grew (new lines, or view was laid out).
        if self._follow_output and not self._is_output_at_bottom():
            self._scroll_output_to_end()

    def _scroll_output_to_end(self):
        # GtkTextView (GTK 4.20) crashes when scrolled before it's realized.
        # Once it's shown, size change of adjustment scrolls it to the end.
        if not self.output_view.get_realized():
            return
        adjustment = self.output_scrolled_window.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
