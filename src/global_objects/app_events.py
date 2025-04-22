from enum import Enum, auto
from typing import Final, final

@final
class AppEvents(Enum):
    OPEN_APP_SECTION = auto() # Args: (section: AppSection)
    PUSH_VIEW = auto() # Push on Main Navigation View (Full window mode). Args: (view: Gtk.Widget), kwargs: (title=<title>).
    PUSH_SECTION = auto() # Like PUSH_VIEW but for pushing by AppSection enum. # Args: (section: AppSection)

@final
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

