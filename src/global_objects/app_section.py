from enum import Enum, auto

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    HOME = auto()
    PROJECTS = auto()
    BUILDS = auto()
    ENVIRONMENTS = auto()
    SNAPSHOTS = auto()
    TEMPLATES = auto()
    BUGS = auto()
    PREFERENCES = auto()
    HELP = auto()
    ABOUT = auto()

