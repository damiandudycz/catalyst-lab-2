from gi.repository import Gtk, GObject, Adw
from .app_section import AppSection, app_section
#from .toolset_details_view import ToolsetDetailsView
#from .toolset_create_view import ToolsetCreateView
from .app_events import app_event_bus, AppEvents

@app_section(title="Overlays", icon="layers-minimalistic-svgrepo-com-symbolic", order=5_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/overlays/overlays_section.ui')
class OverlaysSection(Gtk.Box):
    __gtype_name__ = "OverlaysSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        pass
        #self.content_navigation_view.push_view(ToolsetDetailsView(toolset=toolset), title="Portage overlay details")

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        pass
        #app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetCreateView(installation_in_progress=installation), "New portage overlay", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        pass
        #app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetCreateView(), "New portage overlay", 640, 480)

