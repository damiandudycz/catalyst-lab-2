from gi.repository import Gtk, GObject, Adw
from .app_section import AppSection, app_section
from .releng_details_view import RelengDetailsView
from .releng_create_view import RelengCreateView
from .app_events import app_event_bus, AppEvents

@app_section(title="Releng", icon="book-minimalistic-svgrepo-com-symbolic", order=3_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/releng/releng_section.ui')
class RelengSection(Gtk.Box):
    __gtype_name__ = "RelengSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        self.content_navigation_view.push_view(RelengDetailsView(releng_directory=item) , title="Releng directory details")

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(installation_in_progress=installation), "New releng directory", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(), "New releng directory", 640, 480)

