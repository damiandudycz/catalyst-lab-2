import weakref
from enum import Enum
from typing import final, TypeVar, Generic, Callable, Dict, List, Any, Union
from gi.repository import GLib

EventBusType = TypeVar("EventBusType", bound=Enum)

@final
class EventBus(Generic[EventBusType]):
    def __init__(self, scheduler: Callable[..., Any] = GLib.idle_add):
        self.scheduler = scheduler
        self._subscribers: Dict[EventBusType, List[Union[weakref.ReferenceType, weakref.WeakMethod]]] = {}

    def subscribe(self, event: EventBusType, callback: Callable):
        if event not in self._subscribers:
            self._subscribers[event] = []
        if hasattr(callback, '__self__') and callback.__self__ is not None:
            ref = weakref.WeakMethod(callback)
        else:
            ref = weakref.ref(callback)
        self._subscribers[event].append(ref)

    def emit(self, event: EventBusType, *args, **kwargs):
        callbacks = self._subscribers.get(event, [])
        alive_callbacks = []
        for ref in callbacks:
            callback = ref()
            if callback is not None:
                # Keep only live callbacks
                alive_callbacks.append(ref)
                self.scheduler(callback, *args, **kwargs)
        self._subscribers[event] = alive_callbacks
