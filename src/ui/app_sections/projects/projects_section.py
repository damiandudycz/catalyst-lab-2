from gi.repository import Gtk, Adw
from .app_section import app_section
from .project_create_view import ProjectCreateView
from .project_manager import ProjectManager
from .project_update import ProjectUpdate
from .app_events import app_event_bus, AppEvents
from .git_directory_details_view import GitDirectoryDetailsView

@app_section(title="Projects", icon="notes-minimalistic-svgrepo-com-symbolic", order=6_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/projects/projects_section.ui')
class ProjectsSection(Gtk.Box):
    __gtype_name__ = "ProjectsSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        view = GitDirectoryDetailsView(
            git_directory=item,
            manager_class=ProjectManager,
            update_class=ProjectUpdate
        )
        view.content_navigation_view = self.content_navigation_view
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_child(view)
        self.content_navigation_view.push_view(
            scrolled_window,
            title="Project details"
        )

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectCreateView(installation_in_progress=installation), "New Project", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectCreateView(), "New Project", 640, 480)

