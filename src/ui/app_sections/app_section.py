from enum import Enum, auto

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    HOME = auto()
    ENVIRONMENTS = auto()
    SNAPSHOTS = auto()
    BUILDS = auto()
    HELP = auto()
    ABOUT = auto()

