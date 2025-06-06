from dataclasses import dataclass, field
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
from .root_helper_client import RootHelperClient, ServerResponse, ServerResponseStatusCode, AuthorizationKeeper
from .root_function import root_function
import os
import subprocess
import re
from collections import defaultdict
from .toolset import Toolset

class ToolsetManager:
    _instance = None

    # Note: To get toolset list use Repository.Toolset.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh_snapshots(self):
        pass # TODO

    def add_toolset(self, toolset: Toolset):
        # Remove existing toolset before adding.
        Repository.Toolset.value = [s for s in Repository.Toolset.value if s.id != toolset.id]
        Repository.Toolset.value.append(toolset)

    def remove_toolset(self, toolset: Toolset):
        if os.path.isfile(toolset.squashfs_file):
            os.remove(toolset.squashfs_file)
        Repository.Toolset.value.remove(toolset)

