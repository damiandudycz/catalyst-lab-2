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

@final
class Settings(Serializable):
    """Global application settings."""

    def __init__(self, keep_root_unlocked: bool = False):
        self._keep_root_unlocked = keep_root_unlocked
        self.event_bus: EventBus[SettingsEvents] = EventBus[SettingsEvents]()

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        keep_root_unlocked = data.get("keep_root_unlocked", True)
        return cls(keep_root_unlocked=keep_root_unlocked)

    def serialize(self) -> dict:
        return {
            "keep_root_unlocked": self.keep_root_unlocked
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

Repository.SETTINGS = Repository(cls=Settings, default_factory=Settings)
