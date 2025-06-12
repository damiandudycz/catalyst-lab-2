from __future__ import annotations
from typing import Protocol, Self, TypeVar, Generic, Type
import json
import os
from .runtime_env import RuntimeEnv
from .event_bus import EventBus
from enum import Enum, auto
from typing import final

class Serializable(Protocol):
    def serialize(self) -> dict: ...
    @classmethod
    def init_from(cls, data: dict) -> Self: ...

T = TypeVar("T", bound=Serializable)

class TrackedList(list[T]):
    def __init__(self, initial: list[T], save_callback):
        super().__init__(initial)
        self._save_callback = save_callback

    def _trigger_save(self):
        if callable(self._save_callback):
            self._save_callback()

    def append(self, item: T):
        super().append(item)
        self._trigger_save()

    def extend(self, iterable):
        super().extend(iterable)
        self._trigger_save()

    def insert(self, index: int, item: T):
        super().insert(index, item)
        self._trigger_save()

    def remove(self, item: T):
        super().remove(item)
        self._trigger_save()

    def pop(self, index: int = -1):
        item = super().pop(index)
        self._trigger_save()
        return item

    def clear(self):
        super().clear()
        self._trigger_save()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._trigger_save()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._trigger_save()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._trigger_save()

    def reverse(self):
        super().reverse()
        self._trigger_save()

@final
class RepositoryEvent(Enum):
    VALUE_CHANGED = auto()

class Repository(Generic[T]):

    registered_aliases: list[str] = []

    def __init__(self, cls: Type[T], collection: bool = False, alias: str | None = None, default_factory: Callable[[None],T] | None = None):
        self._cls = cls
        self._collection = collection
        self._alias = alias or (cls.__name__.lower() if not collection else f"{cls.__name__.lower()}_list")
        self._default_factory = default_factory
        self.event_bus: EventBus[RepositoryEvent] = EventBus[RepositoryEvent]()
        if self._alias in Repository.registered_aliases:
            raise RuntimeError(f"Repository with alias {self._alias} is already in use. Use shared instance for the same values.")
        else:
            Repository.registered_aliases.append(self._alias)
        self._value: T | TrackedList[T] = self._load()

    def _config_file(self) -> str:
        config_paths = {
            RuntimeEnv.FLATPAK: lambda: os.path.expanduser(f"~/.var/app/{os.environ.get('FLATPAK_ID')}/config"),
            RuntimeEnv.HOST: lambda: os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        }
        config_base = os.path.join(config_paths.get(RuntimeEnv.current())(), "catalystlab")
        return os.path.join(config_base, f"{self._alias}.json")

    def save(self):
        print(f"Saving: {self}")
        path = self._config_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                if self._collection:
                    if not isinstance(self._value, list):
                        raise TypeError("Expected a list of serializable items.")
                    json.dump({"items": [item.serialize() for item in self._value]}, f, indent=2)
                else:
                    json.dump(self._value.serialize(), f, indent=2)
        except Exception as e:
            raise e
        finally:
            self.event_bus.emit(RepositoryEvent.VALUE_CHANGED, self._value)

    def _load(self) -> T | TrackedList[T]:
        path = self._config_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self._collection:
                items_data = data.get("items", [])
                return TrackedList([self._cls.init_from(item) for item in items_data], self.save)
            else:
                return self._cls.init_from(data)
        except (FileNotFoundError, json.JSONDecodeError, ValueError, AttributeError):
            return TrackedList([], self.save) if self._collection else self._default_factory()

    def _delete(self):
        path = self._config_file()
        if os.path.isfile(path):
            os.remove(path)
        self._value = TrackedList([], self.save) if self._collection else self._default_factory()

    def reset(self):
        self._delete()

    @property
    def value(self) -> T | TrackedList[T]:
        return self._value
    @value.setter
    def value(self, new_value: T | list[T]):
        if self._collection:
            if isinstance(new_value, TrackedList):
                self._value = new_value
                self._value._save_callback = self.save  # update callback if needed
                self.save()
            elif isinstance(new_value, list):
                self._value = TrackedList(new_value, self.save)
                self.save()
            else:
                raise TypeError("Expected a list or TrackedList of serializable items.")
        else:
            self._value = new_value
            self.save()

