from enum import Enum, auto
from typing import final
from typing import TypeVar, Generic, Callable, Dict, List

EventBusType = TypeVar("EventBusType", bound=Enum)  # Enum type variable
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
            callback(*args, **kwargs)

