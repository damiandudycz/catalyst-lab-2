from enum import Enum
from gi.repository import Gtk, GLib

class MainPage(Enum):
    WELCOME = ("Welcome", "go-home-symbolic")
    PROJECTS = ("Projects", "folder-documents-symbolic")
    BUILDS = ("Builds", "emblem-ok-symbolic")
    HELP = ("Help", "help-faq-symbolic")
    ABOUT = ("About", "help-about-symbolic")

    initial_page = WELCOME

    def __init__(self, label: str, icon: str):
        self.label = label
        self.icon = icon

    def create_page(self) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        builder = Gtk.Builder()
        resource_path = f"/com/damiandudycz/CatalystLab/main_sections/{self.name.lower()}.ui"
        try:
            builder.add_from_resource(resource_path)
        except GLib.Error as e:
            print(f"Failed to load resource for {self.name}: {e}")
            # Optionally fall back to a default page
            builder.add_from_resource("/com/damiandudycz/CatalystLab/main_sections/not_implemented.ui")
        return builder.get_objects()[0]
