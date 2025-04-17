from gi.repository import Gtk
from .main_section import MainSection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_sections/welcome_section.ui')
class WelcomeSection(MainSection):
    __gtype_name__ = "WelcomeSection"

    label = "Welcome"
    icon = "aaa"

class ExampleSectionWithoutTemplate(MainSection):
    __gtype_name__ = "ExampleSectionWithoutTemplate"
    label = "ExampleSectionWithoutTemplate"
    icon = ""
