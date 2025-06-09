from .repository import Repository
import os, shutil
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
        if os.path.isfile(toolset.file_path()):
            os.remove(toolset.file_path())
        Repository.Toolset.value.remove(toolset)

    def is_name_available(self, name: str) -> bool:
        if not name:
            return False
        file_path = Toolset.file_path_for_name(name=name)
        return not os.path.exists(file_path)

    def rename_toolset(self, toolset: Toolset, name: str):
        if not self.is_name_available(name=name):
            raise RuntimeError(f"Toolset name {name} is not available")
        new_path = Toolset.file_path_for_name(name=name)
        shutil.move(toolset.file_path(), new_path)
        toolset.name = name
        Repository.Toolset.save()

