from typing import final
from .git_directory import GitDirectory
from .repository import Serializable

@final
class ProjectDirectory(GitDirectory):

    @classmethod
    def base_location(cls) -> str:
        from .repository import Repository
        import os
        return os.path.realpath(
            os.path.expanduser(
                Repository.Settings.value.project_location
            )
        )

    # TODO: Complete this functionality and also create metadata structure
    def parse_metadata(self, dict: dict) -> Serializable:
        """Overwrite in subclasses that use metadata"""
        return None

