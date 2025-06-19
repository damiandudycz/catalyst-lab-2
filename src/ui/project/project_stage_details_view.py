from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage, load_catalyst_targets
from .project_manager import ProjectManager
from .git_directory import GitDirectoryEvent
from .project_stage import ProjectStageEvent
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_stage_details_view.ui')
class ProjectStageDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectStageDetailsView"

    stage_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()
    stage_target_row = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.content_navigation_view = content_navigation_view
        self.monitor_information_changes()
        self.load_targets()
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self.get_root().set_focus(None)
        self.setup_stage_details()

    @Gtk.Template.Callback()
    def on_stage_name_activate(self, sender):
        new_name = self.stage_name_row.get_text()
        if new_name == self.stage.name:
            self.get_root().set_focus(None)
            return
        is_name_available = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text()) or self.stage_name_row.get_text() == self.stage.name
        try:
            if not is_name_available:
                raise RuntimeError(f"Stage name {new_name} is not available")
            ProjectManager.shared().rename_stage(project=self.project_directory, stage=self.stage, name=new_name)
            self.get_root().set_focus(None)
            self.setup_stage_details()
        except Exception as e:
            print(f"Error renaming stage: {e}")
            self.stage_name_row.add_css_class("error")
            self.stage_name_row.grab_focus()

    @Gtk.Template.Callback()
    def on_stage_name_changed(self, sender):
        is_name_available = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text()) or self.stage_name_row.get_text() == self.stage.name
        self.name_used_row.set_visible(not is_name_available)
        self.stage_name_row.remove_css_class("error")

    def setup_stage_details(self):
        self.stage_name_row.set_text(self.stage.name)
        self.stage_target_row.set_subtitle(self.stage.target_name)

    def monitor_information_changes(self):
        self.stage.event_bus.subscribe(
            ProjectStageEvent.NAME_CHANGED,
            self._update_name
        )

    def _update_name(self, data):
        self._page.set_title(self.stage.name)

    def load_targets(self):
        def worker():
            target_names = load_catalyst_targets(toolset=self.project_directory.get_toolset())
            group = []
            target_rows = [
                StageTargetRow(
                    target_name=name,
                    group=group,
                    checked=(name==self.stage.target_name),
                    on_target_type_selected=self.on_target_type_selected
                )
                for name in target_names
            ]
            for target_row in target_rows:
                GLib.idle_add(self.stage_target_row.add_row, target_row)
        threading.Thread(target=worker, daemon=True).start()

    def on_target_type_selected(self, button, value):
        if button.get_active():
            self.stage.target_name = value
            ProjectManager.shared().save_stage(project=self.project_directory, stage=self.stage)
            self.stage_target_row.set_subtitle(self.stage.target_name)

class StageTargetRow(Adw.ActionRow):
    def __init__(self, target_name: str, group: list, checked: bool, on_target_type_selected):
        super().__init__(title=target_name)
        check_button = Gtk.CheckButton()
        check_button.set_active(checked)
        self.add_prefix(check_button)
        if group:
            check_button.set_group(group[0])
        check_button.connect("toggled", on_target_type_selected, target_name)
        group.append(check_button)
        self.set_activatable_widget(check_button)
        self.set_sensitive(True)
