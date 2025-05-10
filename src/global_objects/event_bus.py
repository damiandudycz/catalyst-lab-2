from enum import Enum, auto
from typing import final, TypeVar, Generic, Callable, Dict, List
from gi.repository import GLib

EventBusType = TypeVar("EventBusType", bound=Enum)

@final
class EventBus(Generic[EventBusType]):
    def __init__(self):
        self._subscribers: Dict[EventBusType, List[Callable]] = {}

    def subscribe(self, event: EventBusType, callback: Callable):
        if event not in self._subscribers:
            self._subscribers[event] = []
        self._subscribers[event].append(callback)

    def emit(self, event: EventBusType, *args, **kwargs):
        for callback in self._subscribers.get(event, []):
            # Schedule each callback to run on the main thread
            GLib.idle_add(callback, *args, **kwargs)

