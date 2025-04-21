# Manage various environments used to access external tools.
# Environment could be host, flatpak or chroot.
# Various environments can be used by the application. For example
# some code might run from detected app environment, while other parts
# can run from user specified or host environment.

from __future__ import annotations
import os
from enum import Enum, auto
import json

class RuntimeEnv(Enum):
    """Describes different runtime environment types"""
    HOST    = auto()
    FLATPAK = auto()

    @staticmethod
    def is_app_running_in_flatpak() -> bool:
        return bool(RuntimeEnv.flatpak_id())

    @staticmethod
    def flatpak_id() -> str | None:
        """Returns the flatpak ID if running in a Flatpak environment, or None otherwise."""
        return os.environ.get('FLATPAK_ID')

    @classmethod
    def current(cls) -> "RuntimeEnv":
        """Returns the current runtime environment (FLATPAK or HOST)."""
        return cls.FLATPAK if cls.is_app_running_in_flatpak() else cls.HOST

class ToolsetEnv(Enum):
    SYSTEM   = auto() # Using tools from system, either through HOST or FLATPAK RuntimeEnv.
    EXTERNAL = auto() # Using tools from given .squashfs installation.

class ToolsetEnvHelper:
    """Class used to manage toolset access - catalyst, qemu, releng, etc."""
    def __init__(self, env: ToolsetEnv, **kwargs):
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
    def init_from(cls, data: dict) -> ToolsetEnvHelper:
        try:
            env = ToolsetEnv[data["env"]]
        except KeyError:
            raise ValueError(f"Invalid 'env' value: {data['env']}")
        kwargs = {}
        if env == ToolsetEnv.EXTERNAL:
            squashfs_file = data.get("squashfs_file")
            if not isinstance(squashfs_file, str):
                raise ValueError("Missing or invalid 'squashfs_file' for EXTERNAL environment")
            kwargs["squashfs_file"] = squashfs_file
        return cls(env, **kwargs)

    @staticmethod
    def system() -> ToolsetEnvHelper:
        """Create a ToolsetEnvHelper with the SYSTEM environment."""
        return ToolsetEnvHelper(ToolsetEnv.SYSTEM)

    @staticmethod
    def external(squashfs_file: str) -> ToolsetEnvHelper:
        """Create a ToolsetEnvHelper with the EXTERNAL environment and a specified squashfs file."""
        return ToolsetEnvHelper(ToolsetEnv.EXTERNAL, squashfs_file=squashfs_file)

    def serialize(self) -> dict:
        data = {
            "env": self.env.value,
        }
        if self.env == ToolsetEnv.EXTERNAL:
            data["squashfs_file"] = self.squashfs_file
        return data
