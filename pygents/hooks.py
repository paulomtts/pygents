from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, overload

from pygents.utils import _inject_context_deps, _null_lock, merge_kwargs


class TurnHook(str, Enum):
    BEFORE_RUN = "before_run"
    AFTER_RUN = "after_run"
    ON_TIMEOUT = "on_timeout"
    ON_ERROR = "on_error"
    ON_COMPLETE = "on_complete"


class AgentHook(str, Enum):
    BEFORE_TURN = "before_turn"
    AFTER_TURN = "after_turn"
    ON_TURN_VALUE = "on_turn_value"
    BEFORE_PUT = "before_put"
    AFTER_PUT = "after_put"
    ON_PAUSE = "on_pause"
    ON_RESUME = "on_resume"


class ToolHook(str, Enum):
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    ON_YIELD = "on_yield"


class ContextQueueHook(str, Enum):
    BEFORE_APPEND = "before_append"
    AFTER_APPEND = "after_append"
    BEFORE_CLEAR = "before_clear"
    AFTER_CLEAR = "after_clear"
    ON_EVICT = "on_evict"


class ContextPoolHook(str, Enum):
    BEFORE_ADD = "before_add"
    AFTER_ADD = "after_add"
    BEFORE_REMOVE = "before_remove"
    AFTER_REMOVE = "after_remove"
    BEFORE_CLEAR = "before_clear"
    AFTER_CLEAR = "after_clear"
    ON_EVICT = "on_evict"


HookType = TurnHook | AgentHook | ToolHook | ContextQueueHook | ContextPoolHook


@dataclass
class HookMetadata:
    """Name, description, and run timing of a hook."""

    name: str
    description: str | None

    def dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
        }


class Hook:
    metadata: HookMetadata
    type: HookType | tuple[HookType, ...] | None
    fn: Callable[..., Awaitable[None]]
    lock: asyncio.Lock | None

    def __init__(
        self,
        fn: Callable[..., Awaitable[None]],
        stored_type: HookType | tuple[HookType, ...],
        asyncio_lock: asyncio.Lock | None,
        fixed_kwargs: dict[str, Any],
    ) -> None:
        self.fn = fn
        self.type = stored_type
        self.lock = asyncio_lock
        self.metadata = HookMetadata(fn.__name__, fn.__doc__)
        self._fixed_kwargs = fixed_kwargs
        functools.update_wrapper(self, fn)

    async def __call__(self, *args: Any, **kwargs: Any) -> None:

        merged = merge_kwargs(self._fixed_kwargs, kwargs, f"hook {self.fn.__name__!r}")
        merged = _inject_context_deps(self.fn, merged)
        lock_ctx = self.lock if self.lock is not None else _null_lock
        async with lock_ctx:
            await self.fn(*args, **merged)

    def __repr__(self) -> str:
        return f"Hook(type={self.type!r}, metadata={self.metadata!r})"


@overload
def hook(
    type: HookType,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[None]]], Hook]: ...


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

        asyncio_lock = asyncio.Lock() if lock else None
        wrapper = Hook(fn, stored_type, asyncio_lock, fixed_kwargs)
        HookRegistry.register(wrapper)
        return wrapper

    return decorator
