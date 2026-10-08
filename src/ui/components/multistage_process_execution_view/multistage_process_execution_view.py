from __future__ import annotations
from gi.repository import Gtk, Gdk, GLib, GObject, Adw
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

class MultiStageProcessStageRow(Adw.ExpanderRow):
    """Displays stage state. Can be expanded to show output of commands executed by stage."""

    def __init__(self, step: MultiStageProcessStage, owner: MultistageProcessExecutionView):
        super().__init__(title=step.name, subtitle=step.description)
        self.step = step
        self.owner = owner
        self._setup_output_view()
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
        step.event_bus.subscribe(
            MultiStageProcessStageEvent.OUTPUT_LINE_ADDED,
            self._step_output_line_added
        )

    def _setup_output_view(self):
        self.output_view = Gtk.TextView()
        self.output_view.set_editable(False)
        self.output_view.set_cursor_visible(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.output_view.set_top_margin(8)
        self.output_view.set_bottom_margin(8)
        self.output_view.set_left_margin(8)
        self.output_view.set_right_margin(8)
        self.output_view.add_css_class("transparent-bg")
        self.output_buffer = self.output_view.get_buffer()
        self.output_buffer.set_text("\n".join(self.step.output_lines))
        self.output_scrolled_window = Gtk.ScrolledWindow()
        self.output_scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.output_scrolled_window.set_min_content_height(240)
        self.output_scrolled_window.set_max_content_height(240)
        self.output_scrolled_window.set_child(self.output_view)
        # Keep output scrolled to the bottom while new lines arrive. Stop following when user
        # scrolls up, resume when user scrolls back to the bottom.
        self._follow_output = True
        adjustment = self.output_scrolled_window.get_vadjustment()
        adjustment.connect("value-changed", self._on_output_scrolled)
        adjustment.connect("changed", self._on_output_size_changed)
        frame = Gtk.Frame()
        frame.set_child(self.output_scrolled_window)
        copy_button = Gtk.Button(label="Copy output")
        copy_button.set_halign(Gtk.Align.END)
        copy_button.add_css_class("flat")
        copy_button.connect("clicked", self._on_copy_output_clicked)
        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        output_box.set_margin_top(8)
        output_box.set_margin_bottom(8)
        output_box.set_margin_start(12)
        output_box.set_margin_end(12)
        output_box.append(frame)
        output_box.append(copy_button)
        output_row = Gtk.ListBoxRow()
        output_row.set_activatable(False)
        output_row.set_selectable(False)
        output_row.set_child(output_box)
        self.add_row(output_row)
        # Allow expanding only when there is some output to show.
        # Step might have already failed before view was connected, show its output then.
        self.set_enable_expansion(bool(self.step.output_lines))
        self.set_expanded(self.step.state == MultiStageProcessStageState.FAILED and bool(self.step.output_lines))
        self._bind_arrow_visibility()

    def _bind_arrow_visibility(self):
        """Show expander arrow only when there is output to expand."""
        # AdwExpanderRow doesn't expose its arrow, find it by style class it uses.
        def find_arrow(widget: Gtk.Widget) -> Gtk.Widget | None:
            child = widget.get_first_child()
            while child:
                if isinstance(child, Gtk.Image) and child.has_css_class("expander-row-arrow"):
                    return child
                if found := find_arrow(child):
                    return found
                child = child.get_next_sibling()
            return None
        arrow = find_arrow(self)
        if arrow:
            self.bind_property("enable-expansion", arrow, "visible", GObject.BindingFlags.SYNC_CREATE)

    def _step_output_line_added(self, line: str):
        end_iter = self.output_buffer.get_end_iter()
        self.output_buffer.insert(end_iter, line if self.output_buffer.get_char_count() == 0 else "\n" + line)
        if not self.get_enable_expansion():
            # Enabling expansion also expands the row, keep it collapsed until user opens it.
            # Failed step stays expanded, as its first output line can be the failure reason.
            self.set_enable_expansion(True)
            self.set_expanded(self.step.state == MultiStageProcessStageState.FAILED)

    def _is_output_at_bottom(self) -> bool:
        adjustment = self.output_scrolled_window.get_vadjustment()
        return adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 1

    def _on_output_scrolled(self, adjustment: Gtk.Adjustment):
        self._follow_output = self._is_output_at_bottom()

    def _on_output_size_changed(self, adjustment: Gtk.Adjustment):
        # Content grew (new lines, or row was expanded and laid out).
        if self._follow_output and not self._is_output_at_bottom():
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())

    def _on_copy_output_clicked(self, button):
        Gdk.Display.get_default().get_clipboard().set("\n".join(self.step.output_lines))

    def _step_progress_changed(self, progress: float | None):
        self._update_status_label()

    def _step_state_changed(self, state: MultiStageProcessStageState):
        self.set_sensitive(state != MultiStageProcessStageState.SCHEDULED)
        if state == MultiStageProcessStageState.FAILED and self.step.output_lines:
            self.set_expanded(True) # Show what went wrong.
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

