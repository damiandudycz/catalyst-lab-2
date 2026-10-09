from gi.repository import Gtk, Adw
from .app_section import app_section
from .project_build_process import ProjectBuild
from .status_indicator import combined_status, items_status, processes_status
from .app_events import app_event_bus, AppEvents
from .project_builds_view import ProjectBuildsView
from .project_build_view import ProjectBuildView

@app_section(title="Builds", icon="box-minimalistic-svgrepo-com-symbolic", order=2_500)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/builds/builds_section.ui')
class BuildsSection(Gtk.Box):
    __gtype_name__ = "BuildsSection"

    @staticmethod
    def section_status():
        """Build running (side menu)."""
        return combined_status(processes_status(ProjectBuild))

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        view = ProjectBuildsView(project_directory=item, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=f"{item.name} builds")

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        # Running build, open its progress.
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=installation.project_directory, installation_in_progress=installation), "Build stages", 640, 480)
