from enum import Enum
from typing import Type
from gi.repository import Gtk, GLib
from .welcome_section import WelcomeSection

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    #           Class           Title       Icon                         Show in side bar | Display side bar
    WELCOME  = (WelcomeSection, "Welcome" , "go-home-symbolic"         , True,  False)
    PROJECTS = (Gtk.Button,     "Projects", "folder-documents-symbolic", True,  True)
    BUILDS   = (WelcomeSection, "Builds"  , "emblem-ok-symbolic"       , True,  False)
    HELP     = (WelcomeSection, "Help"    , "help-faq-symbolic"        , True,  False)
    ABOUT    = (WelcomeSection, "About"   , "help-about-symbolic"      , True,  False)

    initial_section = WELCOME

    def __init__(self, module: Type, label: str, icon: str, show_in_side_bar: bool, show_side_bar: bool):
        self.label = label
        self.icon = icon
        self.module = module
        self.show_in_side_bar = show_in_side_bar
        self.show_side_bar = show_side_bar

    def create_section(self) -> Gtk.Widget:
        """Create and return a GTK widget for this page."""
        # TODO: If fails, load error page instead
        return self.module()

