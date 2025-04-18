from enum import Enum

class AppEvents(Enum):
    OPEN_ABOUT = "open_about"
    SAVE_DATA = "save_data"

class EventBus:
    _subscribers = {}

    @classmethod
    def subscribe(cls, event: AppEvents, callback):
        if event not in cls._subscribers:
            cls._subscribers[event] = []
        cls._subscribers[event].append(callback)

    @classmethod
    def emit(cls, event: AppEvents, *args, **kwargs):
        for callback in cls._subscribers.get(event, []):
            callback(*args, **kwargs)

