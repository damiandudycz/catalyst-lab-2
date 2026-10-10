from __future__ import annotations
from typing import final
from enum import Enum, auto
from .event_bus import EventBus
from .repository import Serializable, Repository

@final
class SettingsEvents(Enum):
    KEEP_ROOT_UNLOCKED_CHANGED = auto()
    TOOLSETS_LOCATION_CHANGED = auto()
    SNAPSHOTS_LOCATION_CHANGED = auto()
    RELENG_LOCATION_CHANGED = auto()
    OVERLAY_LOCATION_CHANGED = auto()
    PROJECT_LOCATION_CHANGED = auto()
    BUILDS_LOCATION_CHANGED = auto()
    CACHE_LOCATION_CHANGED = auto()
    TEMPORARY_LOCATION_CHANGED = auto()
    PACKAGES_LOCATION_CHANGED = auto()
    INITIAL_SETUP_DONE_CHANGED = auto()
    AUTO_CLEAN_SNAPSHOTS_CHANGED = auto()

@final
class Settings(Serializable):
    """Global application settings."""

    def __init__(
        self,
        initial_setup_done: bool = False,
        keep_root_unlocked: bool = True,
        toolsets_location: str = "~/CatalystLab/Toolsets",
        snapshots_location: str = "~/CatalystLab/Snapshots",
        releng_location: str = "~/CatalystLab/Releng",
        overlay_location: str = "~/CatalystLab/Overlays",
        project_location: str = "~/CatalystLab/Projects",
        builds_location: str = "~/CatalystLab/Builds",
        cache_location: str = "~/CatalystLab/Cache",
        temporary_location: str = "~/CatalystLab/Temporary",
        packages_location: str = "~/CatalystLab/Packages",
        auto_clean_snapshots: bool = False
    ):
        self._initial_setup_done = initial_setup_done
        self._keep_root_unlocked = keep_root_unlocked
        self._toolsets_location = toolsets_location
        self._snapshots_location = snapshots_location
        self._releng_location = releng_location
        self._overlay_location = overlay_location
        self._project_location = project_location
        self._builds_location = builds_location
        self._cache_location = cache_location # Reusable data, safe to delete (distfiles, extracted squashfs files).
        self._temporary_location = temporary_location # Data of running operations, emptied when app starts.
        self._packages_location = packages_location # Binary packages folders, shared by projects.
        self._auto_clean_snapshots = auto_clean_snapshots # Snapshots not used by projects are removed (except newest).
        self.event_bus = EventBus[SettingsEvents]()

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        try:
            return cls(
                initial_setup_done=data["initial_setup_done"],
                keep_root_unlocked=data["keep_root_unlocked"],
                toolsets_location=data["toolsets_location"],
                snapshots_location=data["snapshots_location"],
                releng_location=data["releng_location"],
                overlay_location=data["overlay_location"],
                project_location=data["project_location"],
                # Added later, settings stored by older versions don't contain it.
                builds_location=data.get("builds_location", "~/CatalystLab/Builds"),
                cache_location=data.get("cache_location", "~/CatalystLab/Cache"),
                temporary_location=data.get("temporary_location", "~/CatalystLab/Temporary"),
                packages_location=data.get("packages_location", "~/CatalystLab/Packages"),
                auto_clean_snapshots=data.get("auto_clean_snapshots", False)
            )
        except:
            return cls()

    def serialize(self) -> dict:
        return {
            "initial_setup_done": self.initial_setup_done,
            "keep_root_unlocked": self.keep_root_unlocked,
            "toolsets_location": self.toolsets_location,
            "snapshots_location": self.snapshots_location,
            "releng_location": self.releng_location,
            "overlay_location": self.overlay_location,
            "project_location": self.project_location,
            "builds_location": self.builds_location,
            "cache_location": self.cache_location,
            "temporary_location": self.temporary_location,
            "packages_location": self.packages_location,
            "auto_clean_snapshots": self.auto_clean_snapshots
        }

    # --------------------------------------------------------------------------
    # Accessors for keep_root_unlocked:

    @property
    def initial_setup_done(self) -> bool:
        return self._initial_setup_done
    @initial_setup_done.setter
    def initial_setup_done(self, value: bool):
        if self._initial_setup_done != value:
            self._initial_setup_done = value
            self.event_bus.emit(
                SettingsEvents.INITIAL_SETUP_DONE_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for keep_root_unlocked:

    @property
    def keep_root_unlocked(self) -> bool:
        return self._keep_root_unlocked
    @keep_root_unlocked.setter
    def keep_root_unlocked(self, value: bool):
        if self._keep_root_unlocked != value:
            self._keep_root_unlocked = value
            self.event_bus.emit(
                SettingsEvents.KEEP_ROOT_UNLOCKED_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for toolsets location:

    @property
    def toolsets_location(self) -> bool:
        return self._toolsets_location
    @toolsets_location.setter
    def toolsets_location(self, value: bool):
        if self._toolsets_location != value:
            self._toolsets_location = value
            self.event_bus.emit(
                SettingsEvents.TOOLSETS_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for snapshots location:

    @property
    def snapshots_location(self) -> bool:
        return self._snapshots_location
    @snapshots_location.setter
    def snapshots_location(self, value: bool):
        if self._snapshots_location != value:
            self._snapshots_location = value
            self.event_bus.emit(
                SettingsEvents.SNAPSHOTS_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for releng location:

    @property
    def releng_location(self) -> bool:
        return self._releng_location
    @releng_location.setter
    def releng_location(self, value: bool):
        if self._releng_location != value:
            self._releng_location = value
            self.event_bus.emit(
                SettingsEvents.RELENG_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for overlay location:

    @property
    def overlay_location(self) -> bool:
        return self._overlay_location
    @overlay_location.setter
    def overlay_location(self, value: bool):
        if self._overlay_location != value:
            self._overlay_location = value
            self.event_bus.emit(
                SettingsEvents.OVERLAY_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for project location:

    @property
    def project_location(self) -> bool:
        return self._project_location
    @project_location.setter
    def project_location(self, value: bool):
        if self._project_location != value:
            self._project_location = value
            self.event_bus.emit(
                SettingsEvents.PROJECT_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()


    # --------------------------------------------------------------------------
    # Accessors for builds location:

    @property
    def builds_location(self) -> str:
        return self._builds_location
    @builds_location.setter
    def builds_location(self, value: str):
        if self._builds_location != value:
            self._builds_location = value
            self.event_bus.emit(
                SettingsEvents.BUILDS_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for cache location:

    @property
    def cache_location(self) -> str:
        return self._cache_location
    @cache_location.setter
    def cache_location(self, value: str):
        if self._cache_location != value:
            self._cache_location = value
            self.event_bus.emit(
                SettingsEvents.CACHE_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for temporary location:

    @property
    def temporary_location(self) -> str:
        return self._temporary_location
    @temporary_location.setter
    def temporary_location(self, value: str):
        if self._temporary_location != value:
            self._temporary_location = value
            self.event_bus.emit(
                SettingsEvents.TEMPORARY_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for packages location:

    @property
    def packages_location(self) -> str:
        return self._packages_location
    @packages_location.setter
    def packages_location(self, value: str):
        if self._packages_location != value:
            self._packages_location = value
            self.event_bus.emit(
                SettingsEvents.PACKAGES_LOCATION_CHANGED,
                value
            )
            Repository.Settings.save()

    # --------------------------------------------------------------------------
    # Accessors for auto clean snapshots:

    @property
    def auto_clean_snapshots(self) -> bool:
        return self._auto_clean_snapshots
    @auto_clean_snapshots.setter
    def auto_clean_snapshots(self, value: bool):
        if self._auto_clean_snapshots != value:
            self._auto_clean_snapshots = value
            self.event_bus.emit(
                SettingsEvents.AUTO_CLEAN_SNAPSHOTS_CHANGED,
                value
            )
            Repository.Settings.save()
