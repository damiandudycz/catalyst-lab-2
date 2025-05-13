from __future__ import annotations
import os
from enum import Enum, auto
import json
from typing import Final, final
import uuid
from .environment import RuntimeEnv

@final
class Toolset:
    """Class containing details of the Toolset instances."""
    """Only metadata, no functionalities."""
    """Functionalities are handled by ToolsetContainer."""
    def __init__(self, env: ToolsetEnv, uuid: UUID, **kwargs):
        self.uuid = uuid
        self.env = env
        match env:
            case ToolsetEnv.SYSTEM:
                pass
            case ToolsetEnv.EXTERNAL:
                self.squashfs_file = kwargs.get("squashfs_file")
                if not isinstance(self.squashfs_file, str):
                    raise ValueError("EXTERNAL requires a 'squashfs_file' keyword argument (str)")
            case _:
                raise ValueError(f"Unknown env: {env}")

    @classmethod
    def init_from(cls, data: dict) -> Toolset:
        try:
            uuid_value = uuid.UUID(data["uuid"])
            env = ToolsetEnv[data["env"]]
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        kwargs = {}
        match env:
            case ToolsetEnv.SYSTEM:
                pass
            case ToolsetEnv.EXTERNAL:
                squashfs_file = data.get("squashfs_file")
                if not isinstance(squashfs_file, str):
                    raise ValueError("Missing or invalid 'squashfs_file' for EXTERNAL environment")
                kwargs["squashfs_file"] = squashfs_file
        return cls(env, uuid_value, **kwargs)

    def serialize(self) -> dict:
        data = {
            "uuid": str(self.uuid),
            "env": self.env.name
        }
        if self.env == ToolsetEnv.EXTERNAL:
            data["squashfs_file"] = self.squashfs_file
        return data

    @staticmethod
    def system() -> Toolset:
        """Create a Toolset with the SYSTEM environment."""
        return Toolset(ToolsetEnv.SYSTEM, uuid.uuid4())

    @staticmethod
    def external(squashfs_file: str) -> Toolset:
        """Create a Toolset with the EXTERNAL environment and a specified squashfs file."""
        return Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), squashfs_file=squashfs_file)

    def is_allowed_in_current_host() -> bool:
        return self.env.is_running_in_gentoo_host()

    def toolset_root(self) -> str:
        match self.env:
            case ToolsetEnv.SYSTEM:
                return "/"
            case ToolsetEnv.EXTERNAL:
                return self.squashfs_file # TODO: For now squashfs_file and root dir are mixed concepts.

@final
class ToolsetEnv(Enum):
    SYSTEM   = auto() # Using tools from system, either through HOST or FLATPAK RuntimeEnv.
    EXTERNAL = auto() # Using tools from given .squashfs installation.

    def is_allowed_in_current_host(self) -> bool:
        """Can selected ToolsetEnv be used in current host. SYSTEM only allowed in gentoo, EXTERNAL allowed anywhere."""
        match self:
            case ToolsetEnv.SYSTEM:
                return RuntimeEnv.is_running_in_gentoo_host()
            case ToolsetEnv.EXTERNAL:
                return True

