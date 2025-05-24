from __future__ import annotations
from typing import Callable, final
import json
import os
from enum import Enum, auto
from uuid import UUID
from .runtime_env import RuntimeEnv
from .event_bus import EventBus
from .repository import Serializable, Repository

@final
class SettingsEvents(Enum):
    KEEP_ROOT_UNLOCKED_CHANGED = auto()
    TOOLSETS_LOCATION_CHANGED = auto()

@final
class Settings(Serializable):
    """Global application settings."""

    def __init__(self, keep_root_unlocked: bool = False, toolsets_location: str = "~/CatalystLab/Toolsets"):
        self._keep_root_unlocked = keep_root_unlocked
        self._toolsets_location = toolsets_location
        self.event_bus: EventBus[SettingsEvents] = EventBus[SettingsEvents]()

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        try:
            keep_root_unlocked = data["keep_root_unlocked"]
            toolsets_location = data["toolsets_location"]
            return cls(keep_root_unlocked=keep_root_unlocked, toolsets_location=toolsets_location)
        except:
            return cls()

    def serialize(self) -> dict:
        return {
            "keep_root_unlocked": self.keep_root_unlocked,
            "toolsets_location": self.toolsets_location
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
            Repository.SETTINGS.value = self # Triggers save()

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
            Repository.SETTINGS.value = self # Triggers save()

Repository.SETTINGS = Repository(cls=Settings, default_factory=Settings)
