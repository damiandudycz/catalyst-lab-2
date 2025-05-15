from enum import Enum
from typing import final, TypeVar, Generic, Callable, Dict, List, Any
from gi.repository import GLib

EventBusType = TypeVar("EventBusType", bound=Enum)

@final
class EventBus(Generic[EventBusType]):
    def __init__(self, scheduler: Callable[..., Any] = GLib.idle_add):
        self.scheduler = scheduler # Function that calls a function on constant thread (for example main thread).
        self._subscribers: Dict[EventBusType, List[Callable]] = {}

    def subscribe(self, event: EventBusType, callback: Callable):
        if event not in self._subscribers:
            self._subscribers[event] = []
        self._subscribers[event].append(callback)

    def emit(self, event: EventBusType, *args, **kwargs):
        for callback in self._subscribers.get(event, []):
            # Schedule each callback to run on the main thread
            self.scheduler(callback, *args, **kwargs)

