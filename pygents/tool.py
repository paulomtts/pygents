import asyncio
import functools
import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Coroutine, Protocol, TypeVar, cast

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


def _coerce_hooks(
    slot: Callable[..., Awaitable[None]] | list[Callable[..., Awaitable[None]]] | None,
    hook_type: ToolHook,
) -> list:
    """Normalize a named-param hook slot to a list and auto-assign .type if missing."""
    if slot is None:
        return []
    items = slot if isinstance(slot, list) else [slot]
    for h in items:
        if not hasattr(h, "type"):
            h.type = hook_type
    return list(items)


def tool(
    func: Callable[..., T] | None = None,
    *,
    lock: bool = False,
    before_invoke: Callable[..., Awaitable[None]] | list[Callable[..., Awaitable[None]]] | None = None,
    on_yield: Callable[..., Awaitable[None]] | list[Callable[..., Awaitable[None]]] | None = None,
    after_invoke: Callable[..., Awaitable[None]] | list[Callable[..., Awaitable[None]]] | None = None,
    hooks: list[Hook] | None = None,
    **fixed_kwargs: Any,
) -> Callable[..., T]:
    """
    Tools contain instructions for how something should be done.

    Any keyword arguments passed to the decorator (other than lock/before_invoke/
    on_yield/after_invoke/hooks) are merged into every invocation; call-time
    kwargs override these.

    Parameters
    ----------
    lock : bool, optional
        If True, concurrent invocations of this tool are serialized with an
        asyncio.Lock. Default False.
    before_invoke : async callable or list of async callables, optional
        Called immediately before the tool runs. Receives the tool's keyword
        arguments as **kwargs (same as the tool's own signature). Also receives
        any context-injected parameters (ContextQueue, ContextPool) if declared.
        Example: async def before(x: int, y: str) -> None: ...
    on_yield : async callable or list of async callables, optional
        Called for each value yielded by an async-generator tool.
        Receives a single positional argument: the yielded value.
        Only fires for async-generator tools; ignored for coroutine tools.
        Example: async def on_yield(value: Any) -> None: ...
    after_invoke : async callable or list of async callables, optional
        Called after the tool returns. Receives a single positional argument:
        the return value for coroutine tools, or a list of all yielded values
        for async-generator tools.
        Example: async def after(result: Any) -> None: ...
    hooks : list of Hook, optional
        Escape hatch for passing pre-typed Hook instances (e.g. from @hook).
        Hooks from named params and hooks= are all collected and fire together.
    **fixed_kwargs
        Fixed keyword arguments merged into every invocation. Call-time kwargs
        override these.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )

        validate_fixed_kwargs(fn, fixed_kwargs, kind="Tool")

        all_hooks: list = (
            _coerce_hooks(before_invoke, ToolHook.BEFORE_INVOKE)
            + _coerce_hooks(on_yield, ToolHook.ON_YIELD)
            + _coerce_hooks(after_invoke, ToolHook.AFTER_INVOKE)
            + (list(hooks) if hooks else [])
        )

        async def _run_hook(hook_type: ToolHook, *args: Any, **kwargs: Any) -> None:
            for h in HookRegistry.get_by_type(hook_type, wrapper.hooks):
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
        wrapper.hooks = all_hooks
        ToolRegistry.register(cast(Tool, wrapper))
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
