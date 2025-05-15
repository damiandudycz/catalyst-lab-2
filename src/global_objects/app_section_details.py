from gi.repository import Gtk, GLib
from gi.repository import Adw
from .welcome_section import WelcomeSection
from .environments_section import EnvironmentsSection
from .app_section import AppSection

# This needs to be kept in separate file, otherwise there will be circular dependencies with modules created by it.
class AppSectionDetails:

    initial_section = AppSection.HOME

    _cache = {}

    SECTION_CONFIG = {
        #                        .module,              .label,         .title,         .icon,                        .show_in_side_bar, .show_side_bar
        AppSection.HOME:         (WelcomeSection,      "Home",         "Catalyst Lab", "go-home-symbolic",           True,  False),
        AppSection.PROJECTS:     (EnvironmentsSection, "Projects",     "Projects",     "preferences-other-symbolic", True,  True),
        AppSection.BUILDS:       (Gtk.Button,          "Builds",       "Builds",       "emblem-ok-symbolic",         True,  True),
        AppSection.ENVIRONMENTS: (EnvironmentsSection, "Environments", "Environments", "preferences-other-symbolic", True,  True),
        AppSection.SNAPSHOTS:    (EnvironmentsSection, "Snapshots",    "Snapshots",    "preferences-other-symbolic", True,  True),
        AppSection.TEMPLATES:    (EnvironmentsSection, "Templates",    "Templates",    "preferences-other-symbolic", True,  True),
        AppSection.BUGS:         (Gtk.Button,          "Bugs",         "Bugs",         "help-faq-symbolic",          True,  True),
        AppSection.PREFERENCES:  (Gtk.Button,          "Preferences",  "Preferences",  "help-faq-symbolic",          True,  True),
        AppSection.HELP:         (Gtk.Button,          "Help",         "Help",         "help-faq-symbolic",          True,  True),
        AppSection.ABOUT:        (WelcomeSection,      "About",        "About",        "help-about-symbolic",        True,  True),
    }

    def __init__(self, section: AppSection):
        # Load config from SECTION_CONFIG and create instance.
        self.module, self.label, self.title, self.icon, self.show_in_side_bar, self.show_side_bar = self.SECTION_CONFIG.get(section)

    @classmethod
    def init_from(cls, section: AppSection) -> "AppSectionDetails":
        # Return cached details for given app section
        if section not in cls._cache:
            cls._cache[section] = cls(section)
        return cls._cache[section]

    def create_section(self, content_navigation_view: Adw.NavigationView) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        """Need to have a reference to embedding content_navigation_view on which it should push it's children."""
        """This could be global navigation_view or content navigation_view, but usually pass content navigation_view here."""
        """Modules can still push on global navigation view using AppEvent if desired."""
        # TODO: If fails, load error page instead
        return self.module(content_navigation_view=content_navigation_view)

