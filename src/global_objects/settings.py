from __future__ import annotations
from typing import List, Callable, final
import json
import os
from enum import Enum, auto
from uuid import UUID
from .runtime_env import RuntimeEnv
from .toolset import Toolset
from .event_bus import EventBus

@final
class SettingsEvents(Enum):
    TOOLSETS_CHANGED = auto()
    KEEP_ROOT_UNLOCKED_CHANGED = auto()

@final
class Settings:
    """Represents application settings, including toolset environments."""

    # TODO: Store opened section here and restore after app relaunch. Default will be Welcome, and it can be moved here from app_section_details.

    _current_instance: Settings | None = None  # Internal cache

    def __init__(self, toolsets: List[Toolset], keep_root_unlocked: bool = False):
        self._toolsets = toolsets
        self._keep_root_unlocked = keep_root_unlocked
        self.event_bus: EventBus[SettingsEvents] = EventBus[SettingsEvents]()

    # Lifecycle and access:

    @staticmethod
    def config_file() -> str:
        """Determine the best writable config path based on runtime environment."""
        config_paths = {
            RuntimeEnv.FLATPAK: lambda: os.path.expanduser(f"~/.var/app/{os.environ.get('FLATPAK_ID')}/config"),
            RuntimeEnv.HOST: lambda: os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        }
        config_base = config_paths.get(RuntimeEnv.current())()
        return os.path.join(config_base, "settings.json")

    @classmethod
    def default(cls) -> Settings:
        """Create a default (empty) Settings instance."""
        return cls(toolsets=[], keep_root_unlocked=False)

    def serialize(self) -> dict:
        """Convert the settings to a serializable dictionary."""
        return {
            "toolsets": [toolset.serialize() for toolset in self._toolsets],
            "keep_root_unlocked": self.keep_root_unlocked
        }

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        """Initialize settings from a dictionary."""
        toolsets_data = data.get("toolsets", [])
        toolsets = [Toolset.init_from(ts) for ts in toolsets_data]
        keep_root_unlocked = data.get("keep_root_unlocked", True)
        return cls(toolsets, keep_root_unlocked=keep_root_unlocked)

    def save(self, path: str = None) -> None:
        """Save current settings to a file (default location if not provided)."""
        path = path or self.config_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.serialize(), f, indent=2)

    @classmethod
    def load(cls, path: str = None) -> Settings:
        """Load settings from a file (default location if not provided)."""
        path = path or cls.config_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.init_from(data)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            print(f"⚠️ Failed to load settings from '{path}': {e}. Falling back to default.")
            return cls.default()

    @classmethod
    def current(cls) -> Settings:
        """Cached settings instance for global access."""
        if cls._current_instance is None:
            cls._current_instance = cls.load()
        return cls._current_instance

    # Toolsets management:

    def get_toolsets(self) -> List[Toolset]:
        return self._toolsets

    def get_toolset_by_id(self, uuid: UUID) -> Toolset | None:
        """Return the toolset that matches the given UUID, or None if not found."""
        for toolset in self._toolsets:
            if getattr(toolset, "uuid", None) == uuid:
                return toolset
        return None

    def get_toolset_matching(self, matching: Callable[[Toolset], bool]) -> Toolset | None:
        for toolset in self._toolsets:
            if matching(toolset):
                return toolset
        return None

    def add_toolset(self, toolset: Toolset):
        self._toolsets.append(toolset)
        self.event_bus.emit(SettingsEvents.TOOLSETS_CHANGED)
        self.save()

    def remove_toolset(self, toolset: Toolset):
        """Remove the specified toolset if it exists in the list."""
        if toolset in self._toolsets:
            self._toolsets.remove(toolset)
            self.event_bus.emit(SettingsEvents.TOOLSETS_CHANGED)
            self.save()

    # Optional: Accessors for keep_root_unlocked

    @property
    def keep_root_unlocked(self) -> bool:
        return self._keep_root_unlocked
    @keep_root_unlocked.setter
    def keep_root_unlocked(self, value: bool):
        if self._keep_root_unlocked != value:
            self._keep_root_unlocked = value
            self.event_bus.emit(SettingsEvents.KEEP_ROOT_UNLOCKED_CHANGED, value)
            self.save()
