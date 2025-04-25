# Manage various environments used to access external tools.
# Environment could be host, flatpak or chroot.
# Various environments can be used by the application. For example
# some code might run from detected app environment, while other parts
# can run from user specified or host environment.

from __future__ import annotations
import os
from enum import Enum, auto
import json
from typing import Final, final
import uuid

@final
class RuntimeEnv(Enum):
    """Describes different runtime environment types and provides some static environment variables"""
    HOST    = auto()
    FLATPAK = auto()

    @staticmethod
    def current() -> RuntimeEnv:
        """Returns the current runtime environment (FLATPAK or HOST)."""
        return RuntimeEnv.FLATPAK if RuntimeEnv.is_app_running_in_flatpak() else RuntimeEnv.HOST

    @staticmethod
    def is_app_running_in_flatpak() -> bool:
        return bool(RuntimeEnv.flatpak_id())

    @staticmethod
    def flatpak_id() -> str | None:
        """Returns the flatpak ID if running in a Flatpak environment, or None otherwise."""
        return os.environ.get('FLATPAK_ID')

    @staticmethod
    def is_running_in_gentoo_host():
        return RuntimeEnv.current()._is_running_in_gentoo_host()

    def _is_running_in_gentoo_host(self):
        """Checks if application is running in Gentoo-based host env.
        Only use with actual current env, will not work otherwise.
        Check once and store it in a class-level cache."""
        attr_name = f"_{self.name.lower()}_is_running_in_gentoo"
        if not hasattr(RuntimeEnv, attr_name):
            try:
                with open(self.resolve_path_for_host_access("/etc/os-release")) as f:
                    os_release = f.read().lower()
                    setattr(RuntimeEnv, attr_name, "id=gentoo" in os_release)
            except FileNotFoundError:
                setattr(RuntimeEnv, attr_name, False)
        return getattr(RuntimeEnv, attr_name)

    def resolve_path_for_host_access(self, path: str) -> str:
        """Converts path to be accessed with flatpak host bridge if needed"""
        """For FLATPAK env adds /run/host prefix to path. For HOST returns without change"""
        match self:
            case RuntimeEnv.HOST:
                return path
            case RuntimeEnv.FLATPAK:
                # Extract the first part of the path (i.e., the directory to check against /run/host)
                path_parts = path.strip("/").split("/", 1)  # Split into first part and the rest of the path
                first_part = path_parts[0] if path_parts else ""
                host_mapped_elements=[ "usr", "bin", "sbin", "lib", "lib64", "lib32", "etc" ]
                # Check first element of path to know if it should be mapped to /run/host
                if first_part in host_mapped_elements:
                    return f"/run/host/{path}".replace("//", "/")
                else:
                    return path

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

@final
class ToolsetEnvHelper:
    """Class used to manage toolset access - catalyst, qemu, releng, etc."""
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
    def init_from(cls, data: dict) -> ToolsetEnvHelper:
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

    @staticmethod
    def system() -> ToolsetEnvHelper:
        """Create a ToolsetEnvHelper with the SYSTEM environment."""
        return ToolsetEnvHelper(ToolsetEnv.SYSTEM, uuid.uuid4())

    @staticmethod
    def external(squashfs_file: str) -> ToolsetEnvHelper:
        """Create a ToolsetEnvHelper with the EXTERNAL environment and a specified squashfs file."""
        return ToolsetEnvHelper(ToolsetEnv.EXTERNAL, uuid.uuid4(), squashfs_file=squashfs_file)

    def serialize(self) -> dict:
        data = {
            "uuid": str(self.uuid),
            "env": self.env.name
        }
        if self.env == ToolsetEnv.EXTERNAL:
            data["squashfs_file"] = self.squashfs_file
        return data

    def is_allowed_in_current_host() -> bool:
        return self.env.is_running_in_gentoo_host()

    def validate(self) -> bool:
        # TODO: Validate if environment contains all required apps and if they are configured correctly.
        # Return errors and warnings if detected.
        pass
