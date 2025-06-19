from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage
from .project_manager import ProjectManager
from .git_directory import GitDirectoryEvent
from .project_stage import ProjectStageEvent

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_stage_details_view.ui')
class ProjectStageDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectStageDetailsView"

    stage_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.content_navigation_view = content_navigation_view
        self.monitor_information_changes()
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

    def monitor_information_changes(self):
        self.stage.event_bus.subscribe(
            ProjectStageEvent.NAME_CHANGED,
            self._update_name
        )

    def _update_name(self, data):
        self._page.set_title(self.stage.name)
