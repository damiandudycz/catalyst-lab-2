from .repository import Repository

# ------------------------------------------------------------------------------
# Registered global repositories:

from .toolset import Toolset
from .settings import Settings
from .snapshot_manager import Snapshot
from .releng_directory import RelengDirectory

Repository.TOOLSETS = Repository(cls=Toolset, collection=True)
Repository.SNAPSHOTS = Repository(cls=Snapshot, collection=True)
Repository.RELENG = Repository(cls=RelengDirectory, collection=True)
Repository.SETTINGS = Repository(cls=Settings, default_factory=Settings)

# ------------------------------------------------------------------------------

