from enum import Enum, auto

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    WELCOME = auto()
    PROJECTS = auto()
    BUILDS = auto()
    HELP = auto()
    ABOUT = auto()

