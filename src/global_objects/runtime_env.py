from __future__ import annotations
import os
from enum import Enum, auto
import json
from typing import Final, final
import uuid

@final
class RuntimeEnv(Enum):
    """Describes different runtime environment types and provides some static environment variables."""
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

