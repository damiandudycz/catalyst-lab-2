from gi.repository import Gtk
from gi.repository import Adw
from .app_section import app_section

@app_section(title="Releng")
class RelengSection(Gtk.Box):
    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)

