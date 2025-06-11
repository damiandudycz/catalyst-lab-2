from gi.repository import Gtk, Adw
from .app_section import app_section
from .overlay_create_view import OverlayCreateView
from .overlay_manager import OverlayManager
from .overlay_update import OverlayUpdate
from .app_events import app_event_bus, AppEvents
from .git_directory_details_view import GitDirectoryDetailsView

@app_section(title="Overlays", icon="layers-minimalistic-svgrepo-com-symbolic", order=5_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/overlays/overlays_section.ui')
class OverlaysSection(Gtk.Box):
    __gtype_name__ = "OverlaysSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        view = GitDirectoryDetailsView(
            git_directory=item,
            manager_class=OverlayManager,
            update_class=OverlayUpdate
        )
        view.set_margin_start(24)
        view.set_margin_end(24)
        view.set_margin_top(24)
        view.set_margin_bottom(24)
        view.content_navigation_view = self.content_navigation_view
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_child(view)
        self.content_navigation_view.push_view(
            scrolled_window,
            title="Portage overlay details"
        )

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, OverlayCreateView(installation_in_progress=installation), "New portage overlay", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, OverlayCreateView(), "New portage overlay", 640, 480)

