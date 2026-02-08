import logging
from typing import Any, Callable, TypeVar

from pygents.errors import SafeExecutionError

R = TypeVar("R")

log = logging.getLogger("pygents")

_function_type = type(lambda: None)


def safe_execution(func: Callable[..., R]) -> Callable[..., R]:
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
        if not getattr(self, "_is_running", False):
            return func(self, *args, **kwargs)
        raise SafeExecutionError(
            f"Skipped <{func.__name__}> call because {self} is running."
        )

    return wrapper


def eval_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v() if isinstance(v, _function_type) else v for k, v in kwargs.items()
    }
