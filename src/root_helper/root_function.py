from __future__ import annotations
from enum import Enum
from typing import Any, Callable
from functools import wraps
from gi.repository import Gio, GLib
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------
# @root_function decorator.
# ------------------------------------------------------------------------------

ROOT_FUNCTION_REGISTRY = {} # Registry for collecting root functions.

def root_function(func):
    """Registers a function and replaces it with a proxy that calls the root server."""
    """All these calls can throw in case server call fails to start."""
    ROOT_FUNCTION_REGISTRY[func.__name__] = func
    @wraps(func)
    def proxy_function(*args, **kwargs):
        from .root_helper_client import RootHelperClient
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            **kwargs
        )
    def _async(handler: callable | None = None, completion_handler: callable | None = None, *args, **kwargs):
        from .root_helper_client import RootHelperClient
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            handler=handler,
            asynchronous=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _raw(*args, completion_handler: callable | None = None, **kwargs):
        from .root_helper_client import RootHelperClient
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            raw=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _async_raw(handler: callable | None = None, completion_handler: callable | None = None, *args, **kwargs):
        from .root_helper_client import RootHelperClient
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            handler=handler,
            asynchronous=True,
            raw=True,
            completion_handler=completion_handler,
            **kwargs
        )
    # Attach variants
    proxy_function._async = _async
    proxy_function._raw = _raw
    proxy_function._async_raw = _async_raw
    return proxy_function
