import asyncio
import functools
import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    ParamSpec,
    TypeVar,
    cast,
    overload,
)

from pygents.errors import UnregisteredHookError
from pygents.hooks import ToolHook
from pygents.hooks import hook as _hook_decorator
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import (
    _inject_context_deps,
    _null_lock,
    merge_kwargs,
)

P = ParamSpec("P")
R = TypeVar("R")
Y = TypeVar("Y")


def _as_tool_hook(fn: Callable[..., Any], hook_type: ToolHook) -> Any:
    """Wrap a plain async function as a registered tool hook, or reuse an existing wrapper.

    Case 1: fn already has .metadata (was processed by hook()) — reuse as-is.
    Case 2: fn was previously wrapped under this name — reuse the existing wrapper.
    Case 3: plain async fn, first time — delegate to hook() to wrap and register.
    """
    if hasattr(fn, "metadata"):
        HookRegistry.register(fn)  # no-op if same object already registered
        return fn

    name = getattr(fn, "__name__", None)
    if name:
        try:
            existing = HookRegistry.get(name)
            if getattr(existing, "fn", None) is fn:
                return existing
        except UnregisteredHookError:
            pass

    return _hook_decorator(hook_type)(fn)


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


class _BaseTool(Generic[P]):
    """Shared base for Tool and AsyncGenTool."""

    fn: Callable[P, Any]
    metadata: ToolMetadata
    lock: asyncio.Lock | None
    hooks: list[tuple[ToolHook, Any]]

    def __init__(
        self,
        fn: Callable[P, Any],
        *,
        lock: bool = False,
        fixed_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.fn = fn
        self.metadata = ToolMetadata(fn.__name__, fn.__doc__)
        self.lock = asyncio.Lock() if lock else None
        self.hooks = []
        self._fixed_kwargs = fixed_kwargs or {}
        functools.update_wrapper(
            cast(Callable[P, Any], self), fn
        )  # ? REASON: make the instance inherit the function's name, docstring, etc.

    @asynccontextmanager
    async def _invoke_context(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        merged = merge_kwargs(self._fixed_kwargs, kwargs, f"tool {self.fn.__name__!r}")
        merged = _inject_context_deps(self.fn, merged)
        lock_ctx = self.lock if self.lock is not None else _null_lock
        async with lock_ctx:
            _start = datetime.now()
            try:
                await self._run_hook(ToolHook.BEFORE_INVOKE, *args, **merged)
                yield merged
            finally:
                self.metadata.start_time = _start
                self.metadata.end_time = datetime.now()

    async def _run_hook(self, hook_type: ToolHook, *args: Any, **kwargs: Any) -> None:
        for stored_type, h in self.hooks:
            if stored_type == hook_type:
                await h(*args, **kwargs)

    def before_invoke(
        self, fn: Callable[P, Awaitable[None]]
    ) -> Callable[P, Awaitable[None]]:
        """Register fn as a BEFORE_INVOKE hook on this tool (method decorator)."""
        wrapped = _as_tool_hook(fn, ToolHook.BEFORE_INVOKE)
        self.hooks.append((ToolHook.BEFORE_INVOKE, wrapped))
        return wrapped

    def after_invoke(
        self, fn: Callable[..., Awaitable[None]]
    ) -> Callable[..., Awaitable[None]]:
        """Register fn as an AFTER_INVOKE hook on this tool (method decorator)."""
        wrapped = _as_tool_hook(fn, ToolHook.AFTER_INVOKE)
        self.hooks.append((ToolHook.AFTER_INVOKE, wrapped))
        return wrapped


class Tool(Generic[P, R], _BaseTool[P]):
    """Typed wrapper for a coroutine tool."""

    fn: Callable[P, Awaitable[R]]

    def after_invoke(
        self, fn: Callable[[R], Awaitable[None]]
    ) -> Callable[[R], Awaitable[None]]:
        """Register fn as an AFTER_INVOKE hook; it receives the tool's return value."""
        return super().after_invoke(fn)  # type: ignore[return-value]

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._invoke_context(args, kwargs) as merged:
            return await self.fn(*args, **merged)


class AsyncGenTool(Generic[P, Y], _BaseTool[P]):
    """Typed wrapper for an async-generator tool."""

    fn: Callable[P, AsyncIterator[Y]]

    def after_invoke(
        self, fn: Callable[[list[Y]], Awaitable[None]]
    ) -> Callable[[list[Y]], Awaitable[None]]:
        """Register fn as an AFTER_INVOKE hook; it receives the list of all yielded values."""
        return super().after_invoke(fn)

    def on_yield(
        self, fn: Callable[[Y], Awaitable[None]]
    ) -> Callable[[Y], Awaitable[None]]:
        """Register fn as an ON_YIELD hook on this tool (method decorator)."""
        wrapped = _as_tool_hook(fn, ToolHook.ON_YIELD)
        self.hooks.append((ToolHook.ON_YIELD, wrapped))
        return wrapped

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AsyncIterator[Y]:
        async with self._invoke_context(args, kwargs) as merged:
            async for value in self.fn(*args, **merged):
                await self._run_hook(ToolHook.ON_YIELD, value)
                yield value


class _ToolDecorator:
    def __init__(self, lock: bool, fixed_kwargs: dict[str, Any]) -> None:
        self._lock = lock
        self._fixed_kwargs = fixed_kwargs

    @overload
    def __call__(self, fn: Callable[P, Awaitable[R]]) -> Tool[P, R]: ...

    @overload
    def __call__(self, fn: Callable[P, AsyncIterator[Y]]) -> AsyncGenTool[P, Y]: ...

    def __call__(
        self, fn: Callable[..., Any]
    ) -> Tool[Any, Any] | AsyncGenTool[Any, Any]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )
        ToolClass: type[Tool[Any, Any]] | type[AsyncGenTool[Any, Any]] = Tool
        if inspect.isasyncgenfunction(fn):
            ToolClass = AsyncGenTool
        instance = ToolClass(fn, lock=self._lock, fixed_kwargs=self._fixed_kwargs)
        ToolRegistry.register(instance)
        return instance


@overload
def tool(func: Callable[P, Awaitable[R]]) -> Tool[P, R]: ...


@overload
def tool(func: Callable[P, AsyncIterator[Y]]) -> AsyncGenTool[P, Y]: ...


@overload
def tool(
    func: None = None,
    *,
    lock: bool = ...,
    **fixed_kwargs: Any,
) -> _ToolDecorator: ...


def tool(
    func: Callable[..., Any] | None = None,
    *,
    lock: bool = False,
    **fixed_kwargs: Any,
) -> Any:
    """
    Tools contain instructions for how something should be done.

    Any keyword arguments passed to the decorator (other than lock) are merged
    into every invocation; call-time kwargs override these.

    Use the method decorators @my_tool.before_invoke / .on_yield / .after_invoke
    to attach lifecycle hooks after decoration.

    Parameters
    ----------
    lock : bool, optional
        If True, concurrent invocations of this tool are serialized with an
        asyncio.Lock. Default False.
    **fixed_kwargs
        Fixed keyword arguments merged into every invocation. Call-time kwargs
        override these.
    """
    decorator = _ToolDecorator(lock, fixed_kwargs)
    if func is not None:
        return decorator(func)
    return decorator
