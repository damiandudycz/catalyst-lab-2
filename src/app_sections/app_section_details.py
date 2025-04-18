from gi.repository import Gtk, GLib
from .welcome_section import WelcomeSection
from .app_section import AppSection

# This needs to be kept in separate file, otherwise there will be circular dependencies with modules created by it.
class AppSectionDetails:

    initial_section = AppSection.WELCOME

    _cache = {}

    SECTION_CONFIG = {
        #                    .module,         .label,     .icon,                       .show_in_side_bar, .show_side_bar
        AppSection.WELCOME:  (WelcomeSection, "Welcome",  "go-home-symbolic",          True,  False),
        AppSection.PROJECTS: (Gtk.Button,     "Projects", "folder-documents-symbolic", True,  True),
        AppSection.BUILDS:   (Gtk.Button,     "Builds",   "emblem-ok-symbolic",        True,  True),
        AppSection.HELP:     (Gtk.Button,     "Help",     "help-faq-symbolic",         True,  True),
        AppSection.ABOUT:    (Gtk.Button,     "About",    "help-about-symbolic",       True,  True),
    }

    def __init__(self, section: AppSection):
        # Load config from SECTION_CONFIG and create instance.
        self.module, self.label, self.icon, self.show_in_side_bar, self.show_side_bar = self.SECTION_CONFIG.get(section)

    @classmethod
    def get(cls, section: AppSection) -> "AppSectionDetails":
        # Return cached details for given app section
        if section not in cls._cache:
            cls._cache[section] = cls(section)
        return cls._cache[section]

    def create_section(self) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        # TODO: If fails, load error page instead
        return self.module()

