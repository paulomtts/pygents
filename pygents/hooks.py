import asyncio
import functools
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol, cast

from pygents.utils import _null_lock


class TurnHook(str, Enum):
    BEFORE_RUN = "before_run"
    AFTER_RUN = "after_run"
    ON_TIMEOUT = "on_timeout"
    ON_ERROR = "on_error"
    ON_VALUE = "on_value"


class AgentHook(str, Enum):
    BEFORE_TURN = "before_turn"
    AFTER_TURN = "after_turn"
    ON_TURN_VALUE = "on_turn_value"
    ON_TURN_ERROR = "on_turn_error"
    ON_TURN_TIMEOUT = "on_turn_timeout"
    BEFORE_PUT = "before_put"
    AFTER_PUT = "after_put"


class ToolHook(str, Enum):
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    ON_YIELD = "on_yield"


class MemoryHook(str, Enum):
    BEFORE_APPEND = "before_append"
    AFTER_APPEND = "after_append"


@dataclass
class HookMetadata:
    """Name, description, and run timing of a hook."""

    name: str
    description: str | None
    start_time: datetime | None = None
    end_time: datetime | None = None

    def dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


class Hook(Protocol):
    metadata: HookMetadata
    hook_type: TurnHook | AgentHook | ToolHook | MemoryHook | None
    fn: Callable[..., Awaitable[None]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None]: ...


def hook(
    hook_type: TurnHook | AgentHook | ToolHook | MemoryHook,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[None]]], Hook]:
    """
    Register an async callable as a typed hook.

    Any keyword arguments passed to the decorator (other than lock) are merged
    into every invocation; call-time kwargs override these.
    """

    def decorator(fn: Callable[..., Awaitable[None]]) -> Hook:
        from pygents.registry import HookRegistry
        from pygents.utils import merge_kwargs, validate_fixed_kwargs

        validate_fixed_kwargs(fn, fixed_kwargs, kind="Hook")
        asyncio_lock = asyncio.Lock() if lock else None

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> None:
            merged = merge_kwargs(fixed_kwargs, kwargs, f"hook {fn.__name__!r}")
            lock_ctx = asyncio_lock if asyncio_lock is not None else _null_lock
            async with lock_ctx:
                wrapper.metadata.start_time = datetime.now()
                try:
                    await fn(*args, **merged)
                finally:
                    wrapper.metadata.end_time = datetime.now()

        wrapper.metadata = HookMetadata(fn.__name__, fn.__doc__)
        wrapper.fn = fn
        wrapper.lock = asyncio_lock
        wrapper.hook_type = hook_type
        HookRegistry.register(cast(Hook, wrapper), name=None, hook_type=hook_type)
        return wrapper

    return decorator
