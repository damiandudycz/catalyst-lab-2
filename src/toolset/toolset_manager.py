from .repository import Repository
import os
from .toolset import Toolset

class ToolsetManager:
    _instance = None

    # Note: To get toolset list use Repository.Toolset.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh(self):
        pass # TODO

    def add_toolset(self, toolset: Toolset):
        # Remove existing toolset before adding.
        Repository.Toolset.value = [s for s in Repository.Toolset.value if s.uuid != toolset.uuid]
        Repository.Toolset.value.append(toolset)

    def remove_toolset(self, toolset: Toolset):
        if os.path.isfile(toolset.squashfs_file):
            os.remove(toolset.squashfs_file)
        Repository.Toolset.value.remove(toolset)

