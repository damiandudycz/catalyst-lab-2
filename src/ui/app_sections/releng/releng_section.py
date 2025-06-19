from gi.repository import Gtk, Adw
from .app_section import app_section
from .releng_create_view import RelengCreateView
from .releng_manager import RelengManager
from .releng_update import RelengUpdate
from .app_events import app_event_bus, AppEvents
from .git_directory_details_view import GitDirectoryDetailsView

@app_section(title="Releng", icon="book-minimalistic-svgrepo-com-symbolic", order=4_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/releng/releng_section.ui')
class RelengSection(Gtk.Box):
    __gtype_name__ = "RelengSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        view = GitDirectoryDetailsView(
            git_directory=item,
            manager_class=RelengManager,
            update_class=RelengUpdate
        )
        view.content_navigation_view = self.content_navigation_view
        view.set_margin_start(24)
        view.set_margin_end(24)
        #view.set_margin_top(24)
        view.set_margin_bottom(24)
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_child(view)
        self.content_navigation_view.push_view(
            scrolled_window,
            title="Releng directory details"
        )

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(installation_in_progress=installation), "New releng directory", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(), "New releng directory", 640, 480)

