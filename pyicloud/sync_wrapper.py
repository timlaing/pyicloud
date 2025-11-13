"""Synchronous wrappers for async functionality."""

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def async_to_sync(async_func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator to convert an async function to a sync function.
    
    This creates a new event loop for each call, making it safe to use
    in synchronous code.
    """
    @wraps(async_func)
    def wrapper(*args, **kwargs):
        try:
            # Try to get the running event loop
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running, create a new one
            loop = None
        
        if loop is not None:
            # We're already in an async context, can't run sync
            raise RuntimeError(
                f"Cannot call sync version of {async_func.__name__} from async context. "
                "Use the async version instead."
            )
        
        # Create a new event loop and run the async function
        return asyncio.run(async_func(*args, **kwargs))
    
    return wrapper


class SyncAsyncWrapper:
    """
    Base class for objects that wrap async objects and provide sync interface.
    """
    
    def __init__(self, async_obj: Any):
        self._async_obj = async_obj
    
    def __getattr__(self, name: str) -> Any:
        """
        Forward attribute access to the async object.
        
        If the attribute is a coroutine function, wrap it to make it sync.
        """
        attr = getattr(self._async_obj, name)
        
        if asyncio.iscoroutinefunction(attr):
            return async_to_sync(attr)
        
        return attr
