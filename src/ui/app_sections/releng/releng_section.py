from gi.repository import Gtk
from gi.repository import Adw
from .app_section import app_section

@app_section(title="Releng", icon="book-minimalistic-svgrepo-com-symbolic", order=4_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/releng/releng_section.ui')
class RelengSection(Gtk.Box):
    __gtype_name__ = "RelengSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)

