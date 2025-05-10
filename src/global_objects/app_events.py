from __future__ import annotations
from enum import Enum, auto
from typing import final
from .event_bus import EventBus

@final
class AppEvents(Enum):
    OPEN_APP_SECTION = auto() # Args: (section: AppSection)
    PUSH_VIEW = auto() # Push on Main Navigation View (Full window mode). Args: (view: Gtk.Widget), kwargs: (title=<title>).
    PUSH_SECTION = auto() # Like PUSH_VIEW but for pushing by AppSection enum. # Args: (section: AppSection)
    # TODO: Move these to client bus.
    CHANGE_ROOT_ACCESS = auto() # root_helper_client unlocked / locked root access
    ROOT_REQUEST_STATUS = auto() # calls when state of root_function is changed (in progress / finished)
    ROOT_REQUEST_WILL_TERMINATE = auto() # Root call was marked to be terminated.

app_event_bus: EventBus[AppEvents] = EventBus[AppEvents]()
