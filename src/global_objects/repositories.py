from .repository import Repository

# ------------------------------------------------------------------------------
# Registered global repositories:

from .toolset import Toolset
from .settings import Settings
from .snapshot import Snapshot
from .releng_directory import RelengDirectory
# Import additional classed so that it can be parsed in repository_list_view:
from .toolset_installation import ToolsetInstallation
from .snapshot_installation import SnapshotInstallation
from .releng_installation import RelengInstallation

Repository.Toolset = Repository(cls=Toolset, collection=True)
Repository.Snapshot = Repository(cls=Snapshot, collection=True)
Repository.RelengDirectory = Repository(cls=RelengDirectory, collection=True)
Repository.Settings = Repository(cls=Settings, default_factory=Settings)

# ------------------------------------------------------------------------------

