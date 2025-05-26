from .repository import Repository

# ------------------------------------------------------------------------------
# Registered global repositories:

from .toolset import Toolset
from .settings import Settings
from .snapshot_manager import Snapshot

Repository.TOOLSETS = Repository(cls=Toolset, collection=True)
Repository.SNAPSHOTS = Repository(cls=Snapshot, collection=True)
Repository.SETTINGS = Repository(cls=Settings, default_factory=Settings)

# ------------------------------------------------------------------------------

