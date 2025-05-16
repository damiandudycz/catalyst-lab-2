from gi.repository import Gtk
from gi.repository import Adw
from .toolset_env_builder import ToolsetEnvBuilder
from urllib.parse import ParseResult
from .architecture import Architecture
import os, re
from datetime import datetime

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_create/toolset_create_view.ui')
class ToolsetCreateView(Gtk.Box):
    __gtype_name__ = "ToolsetCreateView"

    carousel = Gtk.Template.Child()
    configuration_page = Gtk.Template.Child()
    stages_list = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.selected_stage: ParseResult | None = None
        self.architecture = Architecture.HOST
        self.carousel.connect('page-changed', self.on_page_changed)
        ToolsetEnvBuilder.get_stage3_urls(architecture=self.architecture, completion_handler=self._update_stages_result)

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self):
        is_first_page = self.current_page == 0
        is_stage_selected = self.selected_stage is not None
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(not is_first_page and is_stage_selected)
        self.next_button.set_opacity(0.0 if is_first_page else 1.0)

    @Gtk.Template.Callback()
    def on_back_pressed(self, _):
        self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page - 1), True)

    @Gtk.Template.Callback()
    def on_next_pressed(self, _):
        self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.configuration_page, True)

    def _update_stages_result(self, result: list[ParseResult] | Exception):
        self.selected_stage = None
        # Refresh list of results
        if isinstance(result, Exception):
            error_label = Gtk.Label(label=f"Error: {str(result)}")
            self.stages_list.add(error_label)
            self._stage_rows = [error_label]
        else:
            if result:
                # Place recommended first.
                sorted_stages = sorted(result, key=lambda stage: not ToolsetCreateView.is_recommended_stage(
                    os.path.basename(stage.geturl()), self.architecture
                ))
                self.selected_stage = sorted_stages[0]
            if hasattr(self, "_stage_rows"):
                for row in self._stage_rows:
                    self.stages_list.remove(row)
            self._stage_rows = []
            check_buttons_group = []
            for stage in sorted_stages:
                row = Adw.ActionRow()
                filename = os.path.basename(stage.geturl())
                check_button = Gtk.CheckButton(label=filename)
                check_button.set_active(stage == self.selected_stage)
                if check_buttons_group:
                    check_button.set_group(check_buttons_group[0])
                if ToolsetCreateView.is_recommended_stage(filename, self.architecture):
                    # Mark first entry as recommended
                    label = Gtk.Label(label="Recommended")
                    label.add_css_class("dim-label")
                    label.add_css_class("caption")
                    row.add_suffix(label)
                check_button.connect("toggled", self._on_stage_selected, stage)
                check_buttons_group.append(check_button)
                row.add_prefix(check_button)
                row.set_activatable_widget(check_button)
                self.stages_list.add(row)
                self._stage_rows.append(row)

    def _on_stage_selected(self, button: Gtk.CheckButton, stage: ParseResult):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_stage = stage
            print(f"Selected: {stage}")
        else:
            # Deselect if unchecked
            if self.selected_stage == stage:
                self.selected_stage = None
        self.setup_back_next_buttons()

    @staticmethod
    def is_recommended_stage(filename: str, architecture: Architecture) -> bool:
        escaped_arch = re.escape(architecture.value)
        pattern = rf'^stage3-{escaped_arch}-openrc-(\d{{8}}T\d{{6}}Z)\.tar\.xz$'
        match = re.match(pattern, filename)
        if not match:
            return False
        date_str = match.group(1)
        try:
            datetime.strptime(date_str, "%Y%m%dT%H%M%SZ")
            return True
        except ValueError:
            return False

