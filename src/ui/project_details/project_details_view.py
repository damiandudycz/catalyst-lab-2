from gi.repository import Gtk, Adw, GLib
from .project_manager import ProjectManager
from .project_directory import ProjectDirectory
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project_details/project_details_view.ui')
class ProjectDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectDetailsView"

    directory_details_view = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.directory_details_view.setup(git_directory=project_directory, content_navigation_view=self.content_navigation_view)

