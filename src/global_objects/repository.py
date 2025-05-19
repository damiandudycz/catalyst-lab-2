from __future__ import annotations
from typing import Protocol, Self, TypeVar, Generic, Type
import json
import os
from .runtime_env import RuntimeEnv

class Serializable(Protocol):
    def serialize(self) -> dict: ...
    @classmethod
    def init_from(cls, data: dict) -> Self: ...

T = TypeVar("T", bound=Serializable)

class Repository(Generic[T]):
    def __init__(self, cls: Type[T], collection: bool = False, alias: str | None = None):
        self._cls = cls
        self._collection = collection
        self._alias = alias or (cls.__name__.lower() if not collection else f"{cls.__name__.lower()}_list")

    def _config_file(self) -> str:
        config_paths = {
            RuntimeEnv.FLATPAK: lambda: os.path.expanduser(f"~/.var/app/{os.environ.get('FLATPAK_ID')}/config"),
            RuntimeEnv.HOST: lambda: os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        }
        config_base = os.path.join(config_paths.get(RuntimeEnv.current())(), "catalystlab")
        return os.path.join(config_base, f"{self._alias}.json")

    def save(self, obj: T | list[T]):
        path = self._config_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            if self._collection:
                if not isinstance(obj, list):
                    raise TypeError("Expected a list of serializable items.")
                json.dump({"items": [item.serialize() for item in obj]}, f, indent=2)
            else:
                json.dump(obj.serialize(), f, indent=2)

    def load(self) -> T | list[T] | None:
        path = self._config_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self._collection:
                items_data = data.get("items", [])
                return [self._cls.init_from(item) for item in items_data]
            else:
                return self._cls.init_from(data)
        except (FileNotFoundError, json.JSONDecodeError, ValueError, AttributeError) as e:
            return [] if self._collection else None

    def delete(self) -> None:
        path = self._config_file()
        if os.path.isfile(path):
            os.remove(path)

