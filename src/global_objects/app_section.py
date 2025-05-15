from enum import Enum, auto

class AppSection(Enum):
    # Main sections of the application, as displayed in side menu:
    HOME = auto()
    PROJECTS = auto()
    BUILDS = auto()
    ENVIRONMENTS = auto()
    SNAPSHOTS = auto()
    RELENG = auto()
    TEMPLATES = auto()
    BUGS = auto()
    PREFERENCES = auto()
    HELP = auto()
    ABOUT = auto()

import inspect
from typing import Type, List
from dataclasses import dataclass
from gi.repository import Adw

class AppSectionNew:
    """This is both - metadata for sections, and a repository for all sections."""
    # Shared
    all_sections: List[Type] = []

    def __init__(self, cls, label: str, title: str, icon: str, show_in_side_bar: bool, show_side_bar: bool):
        self.cls = cls
        self.label = label
        self.title = title
        self.icon = icon
        self.show_in_side_bar = show_in_side_bar
        self.show_side_bar = show_side_bar

def app_section(label: str, title: str, icon: str, show_in_side_bar: bool = True, show_side_bar: bool = True):
    def decorator(cls: Type):
        # Validate __init__ signature:
        init = cls.__init__
        sig = inspect.signature(init)
        params = list(sig.parameters.values())
        if (
            len(params) < 2
            or params[0].name != "self"
            or params[1].name != "content_navigation_view"
            or params[1].annotation is not Adw.NavigationView
            or not any(p.kind == p.VAR_KEYWORD for p in params)
        ):
            raise TypeError(f"{cls.__name__} must implement __init__(self, content_navigation_view: Adw.NavigationView, **kwargs)")

        # Register class by name and in list:
        setattr(AppSectionNew, cls.__name__, cls)
        AppSectionNew.all_sections.append(cls)

        # Store metadata:
        cls.section_details = AppSectionNew(cls=cls, label=label, title=title, icon=icon, show_in_side_bar=show_in_side_bar, show_side_bar=show_side_bar)

        return cls
    return decorator

#AppSectionNew.WelcomeSection == WelcomeSection
#AppSectionNew.all_sections == [WelcomeSection]
#WelcomeSection.section_details == AppSectionNew
#WelcomeSection.section_details.cls = WelcomeSection
