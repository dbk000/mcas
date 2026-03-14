"""
Unified Task Registry — §3.0.1 canonical control surface.
==========================================================

Maps explicitly declared task names to handler callables.
No dynamic imports. No title-based string matching.
Tasks are registered at service startup and dispatched by name.

Usage:
    from governance.task_registry import TaskRegistry

    # Registration (at startup)
    TaskRegistry.register("source_reliability", handler_fn)

    # Dispatch
    handler = TaskRegistry.get_task("source_reliability")
    result = handler(cursor)

    # Iteration
    for name in TaskRegistry.registered_names():
        ...
"""

from typing import Callable, Dict, Any


class TaskRegistry:
    _tasks: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str, func: Callable):
        """Register a task handler by canonical name."""
        if name in cls._tasks:
            raise ValueError(f"Task '{name}' is already registered.")
        cls._tasks[name] = func

    @classmethod
    def execute(cls, name: str, *args, **kwargs) -> Any:
        func = cls._tasks.get(name)
        if not func:
            raise KeyError(f"Task '{name}' not found in TaskRegistry.")
        return func(*args, **kwargs)

    @classmethod
    def get_task(cls, name: str) -> Callable:
        func = cls._tasks.get(name)
        if not func:
            raise KeyError(f"Task '{name}' not found in TaskRegistry.")
        return func

    @classmethod
    def registered_names(cls) -> list[str]:
        return list(cls._tasks.keys())

    @classmethod
    def as_api_dict(cls) -> Dict[str, Dict[str, str]]:
        """Return task metadata in the shape the API expects."""
        return {name: {"name": name, "status": "registered"} for name in cls._tasks}

    @classmethod
    def clear(cls):
        """Reset registry (for testing)."""
        cls._tasks.clear()
