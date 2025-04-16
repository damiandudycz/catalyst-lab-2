from enum import Enum

class MainPage(Enum):
    WELCOME = ("Welcome", "user-home-symbolic")
    PROJECTS = ("Projects", "folder-symbolic")
    BUILDS = ("Builds", "build-symbolic")

    initial_page = WELCOME

    def __init__(self, label: str, icon: str):
        self.label = label
        self.icon = icon

