from enum import Enum
from typing import Type
from gi.repository import Gtk, GLib
from .welcome_section import WelcomeSection

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    WELCOME  = (WelcomeSection, "Welcome" , "go-home-symbolic"         )
    PROJECTS = (WelcomeSection, "Projects", "folder-documents-symbolic")
    BUILDS   = (WelcomeSection, "Builds"  , "emblem-ok-symbolic"       )
    HELP     = (WelcomeSection, "Help"    , "help-faq-symbolic"        )
    ABOUT    = (WelcomeSection, "About"   , "help-about-symbolic"      )

    initial_section = WELCOME

    def __init__(self, module: Type, label: str, icon: str):
        self.label = label
        self.icon = icon
        self.module = module

    def create_section(self) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        # TODO: If fails, load error page instead
        return self.module()

