from __future__ import annotations
from typing import List
import json
import os
from .environment import RuntimeEnv, ToolsetEnvHelper
from typing import final

@final
class Settings:
    """Represents application settings, including toolset environments."""

    # TODO: Store opened section here and restore after app relaunch. Default will be Welcome, and it can be moved here from app_section_details.

    _current_instance: Settings | None = None  # Internal cache

    def __init__(self, toolsets: List[ToolsetEnvHelper]):
        self.toolsets = toolsets

    @staticmethod
    def config_file() -> str:
        """Determine the best writable config path based on runtime environment."""
        config_paths = {
            RuntimeEnv.FLATPAK: lambda: os.path.expanduser(f"~/.var/app/{os.environ.get('FLATPAK_ID')}/config"),
            RuntimeEnv.HOST: lambda: os.environ["XDG_CONFIG_HOME"]
        }
        # Get the appropriate config base directory based on current runtime environment
        config_base = config_paths.get(RuntimeEnv.current())()
        return os.path.join(config_base, "settings.json")

    @classmethod
    def default(cls) -> Settings:
        """Create a default (empty) Settings instance."""
        return cls(toolsets=[])

    def serialize(self) -> dict:
        """Convert the settings to a serializable dictionary."""
        return {
            "toolsets": [toolset.serialize() for toolset in self.toolsets]
        }

    @classmethod
    def init_from(cls, data: dict) -> Settings:
        """Initialize settings from a dictionary."""
        toolsets_data = data.get("toolsets", [])
        toolsets = [ToolsetEnvHelper.init_from(ts) for ts in toolsets_data]
        return cls(toolsets)

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
    @property
    def current(cls) -> Settings:
        """Cached settings instance for global access."""
        if cls._current_instance is None:
            cls._current_instance = cls.load()
        return cls._current_instance

    def __repr__(self):
        return f"Settings(toolsets={self.toolsets})"

