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

@final
class Settings(Serializable):
    """Global application settings."""

    def __init__(self, keep_root_unlocked: bool = True, toolsets_location: str = "~/CatalystLab/Toolsets", snapshots_location: str = "~/CatalystLab/Snapshots", releng_location: str = "~/CatalystLab/Releng", overlay_location: str = "~/CatalystLab/Overlays", project_location: str = "~/CatalystLab/Projects"):
        self._keep_root_unlocked = keep_root_unlocked
        self._toolsets_location = toolsets_location
        self._snapshots_location = snapshots_location
        self._releng_location = releng_location
        self._overlay_location = overlay_location
        self._project_location = project_location
        self.event_bus = EventBus[SettingsEvents]()

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        try:
            keep_root_unlocked = data["keep_root_unlocked"]
            toolsets_location = data["toolsets_location"]
            snapshots_location = data["snapshots_location"]
            releng_location = data["releng_location"]
            overlay_location = data["overlay_location"]
            project_location = data["project_location"]
            return cls(
                keep_root_unlocked=keep_root_unlocked,
                toolsets_location=toolsets_location,
                snapshots_location=snapshots_location,
                releng_location=releng_location,
                overlay_location=overlay_location,
                project_location=project_location
            )
        except:
            return cls()

    def serialize(self) -> dict:
        return {
            "keep_root_unlocked": self.keep_root_unlocked,
            "toolsets_location": self.toolsets_location,
            "snapshots_location": self.snapshots_location,
            "releng_location": self.releng_location,
            "overlay_location": self.overlay_location,
            "project_location": self.project_location
        }

    # --------------------------------------------------------------------------
    # Accessors for keep_root_unlocked:

    @property
    def keep_root_unlocked(self) -> bool:
        return self._keep_root_unlocked
    @keep_root_unlocked.setter
    def keep_root_unlocked(self, value: bool):
        if self._keep_root_unlocked != value:
            self._keep_root_unlocked = value
            self.event_bus.emit(SettingsEvents.KEEP_ROOT_UNLOCKED_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

    # --------------------------------------------------------------------------
    # Accessors for toolsets location:

    @property
    def toolsets_location(self) -> bool:
        return self._toolsets_location
    @toolsets_location.setter
    def toolsets_location(self, value: bool):
        if self._toolsets_location != value:
            self._toolsets_location = value
            self.event_bus.emit(SettingsEvents.TOOLSETS_LOCATION_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

    # --------------------------------------------------------------------------
    # Accessors for snapshots location:

    @property
    def snapshots_location(self) -> bool:
        return self._snapshots_location
    @snapshots_location.setter
    def snapshots_location(self, value: bool):
        if self._snapshots_location != value:
            self._snapshots_location = value
            self.event_bus.emit(SettingsEvents.SNAPSHOTS_LOCATION_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

    # --------------------------------------------------------------------------
    # Accessors for releng location:

    @property
    def releng_location(self) -> bool:
        return self._releng_location
    @releng_location.setter
    def releng_location(self, value: bool):
        if self._releng_location != value:
            self._releng_location = value
            self.event_bus.emit(SettingsEvents.RELENG_LOCATION_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

    # --------------------------------------------------------------------------
    # Accessors for overlay location:

    @property
    def overlay_location(self) -> bool:
        return self._overlay_location
    @overlay_location.setter
    def overlay_location(self, value: bool):
        if self._overlay_location != value:
            self._overlay_location = value
            self.event_bus.emit(SettingsEvents.OVERLAY_LOCATION_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

    # --------------------------------------------------------------------------
    # Accessors for project location:

    @property
    def project_location(self) -> bool:
        return self._overlay_location
    @project_location.setter
    def project_location(self, value: bool):
        if self._project_location != value:
            self._project_location = value
            self.event_bus.emit(SettingsEvents.PROJECT_LOCATION_CHANGED, value)
            Repository.Settings.value = self # Triggers save()

