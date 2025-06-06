from .repository import Repository

# ------------------------------------------------------------------------------
# Registered global repositories:

from .toolset import Toolset
from .snapshot import Snapshot
from .releng_directory import RelengDirectory
from .settings import Settings

Repository.Toolset = Repository(cls=Toolset, collection=True)
Repository.Snapshot = Repository(cls=Snapshot, collection=True)
Repository.RelengDirectory = Repository(cls=RelengDirectory, collection=True)
Repository.Settings = Repository(cls=Settings, default_factory=Settings)

# ------------------------------------------------------------------------------

