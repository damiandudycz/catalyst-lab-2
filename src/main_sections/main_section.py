from enum import Enum
from gi.repository import Gtk, GLib

class MainSection(Enum):
    WELCOME = ("Welcome", "go-home-symbolic")
    PROJECTS = ("Projects", "folder-documents-symbolic")
    BUILDS = ("Builds", "emblem-ok-symbolic")
    HELP = ("Help", "help-faq-symbolic")
    ABOUT = ("About", "help-about-symbolic")

    initial_section = WELCOME

    def __init__(self, label: str, icon: str):
        self.label = label
        self.icon = icon

    def create_section(self) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        ui_resource_path = f"/com/damiandudycz/CatalystLab/main_sections/{self.name.lower()}.ui"
        ui_not_implemented_resource_path = f"/com/damiandudycz/CatalystLab/main_sections/not_implemented.ui"
        builder = Gtk.Builder()
        builder.add_from_resource(ui_resource_path)
        return builder.get_objects()[0]

