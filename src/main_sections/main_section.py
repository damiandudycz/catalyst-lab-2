from typing import Type
from gi.repository import Gtk
from .gtkboxabcmeta import GtkBoxABCMeta

class MainSection(Gtk.Box, metaclass=GtkBoxABCMeta):
    """This class is an abstract definition for Main Sections in the application."""
    """Everything that implements this will be shown as entry in side menu."""
    __gtype_name__ = "MainSection"

    label: str
    icon: str

    @classmethod
    def create_section(cls) -> Gtk.Widget:
        return cls()

    @classmethod
    def list_sections(cls) -> [Type]:
        # TODO: Add some sorting by importance or name
        return MainSection.__subclasses__()

