from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, cast, overload

from pygents.utils import _null_lock

if TYPE_CHECKING:
    from pygents.agent import Agent
    from pygents.context_pool import ContextItem, ContextPool
    from pygents.turn import Turn


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


class ContextQueueHook(str, Enum):
    BEFORE_APPEND = "before_append"
    AFTER_APPEND = "after_append"


class ContextPoolHook(str, Enum):
    BEFORE_ADD    = "before_add"
    AFTER_ADD     = "after_add"
    BEFORE_REMOVE = "before_remove"
    AFTER_REMOVE  = "after_remove"
    BEFORE_CLEAR  = "before_clear"
    AFTER_CLEAR   = "after_clear"


HookType = TurnHook | AgentHook | ToolHook | ContextQueueHook | ContextPoolHook


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
    type: HookType | tuple[HookType, ...] | None
    fn: Callable[..., Awaitable[None]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None]: ...


@overload
def hook(
    type: TurnHook.BEFORE_RUN | TurnHook.AFTER_RUN | TurnHook.ON_TIMEOUT,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Turn"], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: TurnHook.ON_ERROR,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Turn", BaseException], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: TurnHook.ON_VALUE,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Turn", Any], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: AgentHook.BEFORE_TURN,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Agent"], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: AgentHook.BEFORE_PUT
    | AgentHook.AFTER_PUT
    | AgentHook.ON_TURN_TIMEOUT
    | AgentHook.AFTER_TURN,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Agent", "Turn"], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: AgentHook.ON_TURN_VALUE,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Agent", "Turn", Any], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: AgentHook.ON_TURN_ERROR,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["Agent", "Turn", BaseException], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: ToolHook.BEFORE_INVOKE,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: ToolHook.ON_YIELD | ToolHook.AFTER_INVOKE,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[[Any], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: ContextQueueHook.BEFORE_APPEND | ContextQueueHook.AFTER_APPEND,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[[list[Any]], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: ContextPoolHook.BEFORE_ADD
    | ContextPoolHook.AFTER_ADD
    | ContextPoolHook.BEFORE_REMOVE
    | ContextPoolHook.AFTER_REMOVE,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["ContextPool", "ContextItem[Any]"], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: ContextPoolHook.BEFORE_CLEAR | ContextPoolHook.AFTER_CLEAR,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[["ContextPool"], Awaitable[None]]], Hook]: ...


@overload
def hook(
    type: list[HookType],
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[None]]], Hook]: ...


def hook(
    type: HookType | list[HookType],
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[None]]], Hook]:
    """
    Register an async callable as a typed hook.

    Pass one or more hook types; the hook will match for each. Multi-type
    hooks (list) must accept *args, **kwargs since different types receive
    different arguments.

    Any keyword arguments passed to the decorator (other than lock) are merged
    into every invocation; call-time kwargs override these.
    """
    types = type if isinstance(type, list) else [type]
    if not types:
        raise ValueError("type requires at least one type")
    stored_type: HookType | tuple[HookType, ...] = (
        types[0] if len(types) == 1 else tuple(types)
    )

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
        wrapper.type = stored_type
        HookRegistry.register(cast(Hook, wrapper), name=None, hook_type=stored_type)
        return wrapper

    return decorator
