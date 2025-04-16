from enum import Enum

class MainPage(Enum):
    WELCOME = ("Welcome", "user-home-symbolic")
    PROJECTS = ("Projects", "folder-symbolic")
    BUILDS = ("Builds", "build-symbolic")

    def __init__(self, label, icon):
        self._label = label
        self._icon = icon

    @property
    def name(self):
        return self._label

    @property
    def icon(self):
        return self._icon
