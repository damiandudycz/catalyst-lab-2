from gi.repository import Gtk, Adw
from .app_section import app_section
from .app_events import app_event_bus, AppEvents
from .deploy_create_view import DeployCreateView
from .project_deploy_view import ProjectDeployView

@app_section(title="Deploy", icon="deploy-symbolic", order=7_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/deploy/deploy_section.ui')
class DeploySection(Gtk.Box):
    __gtype_name__ = "DeploySection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        view = ProjectDeployView(project_directory=item, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=f"Deploy {item.name}")

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        # Running or finished deployment, open its progress.
        app_event_bus.emit(AppEvents.PRESENT_VIEW, DeployCreateView(installation_in_progress=installation), "Deploy build", 640, 560)
