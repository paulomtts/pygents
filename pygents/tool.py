import asyncio
import functools
import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Coroutine, Protocol, TypeVar, cast

from pygents.hooks import Hook, ToolHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import _inject_context_deps, _null_lock, merge_kwargs, validate_fixed_kwargs

T = TypeVar("T", bound=Any)


@dataclass
class ToolMetadata:
    """Name, description, and run timing of a tool."""

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


class Tool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., AsyncIterator[Any]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def tool(
    func: Callable[..., T] | None = None,
    *,
    lock: bool = False,
    hooks: list[Hook] | None = None,
    **fixed_kwargs: Any,
) -> Callable[..., T]:
    """
    Tools contain instructions for how something should be done.

    Any keyword arguments passed to the decorator (other than lock/hooks) are
    merged into every invocation; call-time kwargs override these.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )

        validate_fixed_kwargs(fn, fixed_kwargs, kind="Tool")

        async def _run_hook(hook_type: ToolHook, *args: Any, **kwargs: Any) -> None:
            if h := HookRegistry.get_by_type(hook_type, wrapper.hooks):
                await h(*args, **kwargs)

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                merged = merge_kwargs(fixed_kwargs, kwargs, f"tool {fn.__name__!r}")
                merged = _inject_context_deps(fn, merged)
                lock_ctx = wrapper.lock if wrapper.lock is not None else _null_lock
                async with lock_ctx:
                    wrapper.metadata.start_time = datetime.now()
                    try:
                        await _run_hook(ToolHook.BEFORE_INVOKE, *args, **merged)
                        values: list = []
                        async for value in fn(*args, **merged):
                            await _run_hook(ToolHook.ON_YIELD, value)
                            values.append(value)
                            yield value
                    finally:
                        wrapper.metadata.end_time = datetime.now()
        else:

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                merged = merge_kwargs(fixed_kwargs, kwargs, f"tool {fn.__name__!r}")
                merged = _inject_context_deps(fn, merged)
                lock_ctx = wrapper.lock if wrapper.lock is not None else _null_lock
                async with lock_ctx:
                    wrapper.metadata.start_time = datetime.now()
                    try:
                        await _run_hook(ToolHook.BEFORE_INVOKE, *args, **merged)
                        result = await fn(*args, **merged)
                        return result
                    finally:
                        wrapper.metadata.end_time = datetime.now()

        wrapper.fn = fn
        wrapper.metadata = ToolMetadata(fn.__name__, fn.__doc__)
        wrapper.lock = asyncio.Lock() if lock else None
        wrapper.hooks = list(hooks) if hooks else []
        ToolRegistry.register(cast(Tool, wrapper))
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
