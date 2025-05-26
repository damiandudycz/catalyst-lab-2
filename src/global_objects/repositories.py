from .repository import Repository

# ------------------------------------------------------------------------------
# Registered global repositories:

from .toolset import Toolset
from .settings import Settings

Repository.TOOLSETS = Repository(cls=Toolset, collection=True)
Repository.SETTINGS = Repository(cls=Settings, default_factory=Settings)

# ------------------------------------------------------------------------------

