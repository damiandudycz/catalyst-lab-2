# Manage various environments used to access external tools.
# Environment could be host, flatpak or chroot.
# Various environments can be used by the application. For example
# some code might run from detected app environment, while other parts
# can run from user specified or host environment.

import os
from enum import Enum

class EnvType:
    """Describes different runtime environment types"""
    HOST    = "HOST"
    FLATPAK = "FLATPAK"
    CHROOT  = "CHROOT"

class Env:
    """Contains runtime environment properties - root location, permissions, etc."""

    # Note: To create custom chroot env use:
    # Env(EnvType.CHROOT, location="/mnt/chroot_root")

    def __init__(self, env_type: EnvType, **kwargs):
        self.env_type = env_type

        match env_type:

            case EnvType.HOST:
                self.fs_root = "/"

            case EnvType.FLATPAK:
                self.fs_root = "/run/host/"

            case EnvType.CHROOT:
                location = kwargs.get("location")
                if not isinstance(location, str):
                    raise ValueError("CHROOT requires a 'location' keyword argument (str)")
                self.fs_root = location

            case _:
                raise ValueError(f"Unknown env type: {envType}")

    def verbose(self):
        """Prints details about this environment"""
        print(f"Environment details of {self}:")
        print(f"Env type: {self.env_type}")
        print(f"Env fs_root: {self.fs_root}")

def is_app_running_in_flatpak() -> bool:
    # Check environment variable used by Flatpak
    return "FLATPAK_ID" in os.environ

EnvType.runtime_env_type: EnvType = EnvType.FLATPAK if is_app_running_in_flatpak() else EnvType.HOST
Env.runtime_env: Env = Env(EnvType.runtime_env_type)

