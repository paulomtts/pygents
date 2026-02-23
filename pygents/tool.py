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
    Concatenate,
    Generic,
    ParamSpec,
    TypeVar,
    cast,
    overload,
)

from pygents.context import ContextPool, ContextQueue
from pygents.hooks import Hook, ToolHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import (
    build_method_decorator,
    inject_context_deps,
    merge_kwargs,
    null_lock,
)

P = ParamSpec("P")
R = TypeVar("R")
Y = TypeVar("Y")

BeforeInvokeFn = (
    Callable[P, Awaitable[None]]
    | Callable[Concatenate[ContextQueue, P], Awaitable[None]]
    | Callable[Concatenate[ContextPool, P], Awaitable[None]]
    | Callable[Concatenate[ContextQueue, ContextPool, P], Awaitable[None]]
    | Callable[Concatenate[ContextPool, ContextQueue, P], Awaitable[None]]
)

AfterInvokeFn = (
    Callable[[R], Awaitable[None]]
    | Callable[[R, ContextQueue], Awaitable[None]]
    | Callable[[R, ContextPool], Awaitable[None]]
    | Callable[[R, ContextQueue, ContextPool], Awaitable[None]]
    | Callable[[R, ContextPool, ContextQueue], Awaitable[None]]
)

AfterInvokeGenFn = (
    Callable[[list[Y]], Awaitable[None]]
    | Callable[[list[Y], ContextQueue], Awaitable[None]]
    | Callable[[list[Y], ContextPool], Awaitable[None]]
    | Callable[[list[Y], ContextQueue, ContextPool], Awaitable[None]]
    | Callable[[list[Y], ContextPool, ContextQueue], Awaitable[None]]
)

OnYieldFn = (
    Callable[[Y], Awaitable[None]]
    | Callable[[Y, ContextQueue], Awaitable[None]]
    | Callable[[Y, ContextPool], Awaitable[None]]
    | Callable[[Y, ContextQueue, ContextPool], Awaitable[None]]
    | Callable[[Y, ContextPool, ContextQueue], Awaitable[None]]
)


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

    async def _run_hooks(self, hook_type: ToolHook, *args: Any, **kwargs: Any) -> None:
        await HookRegistry.fire(
            hook_type, [h for t, h in self.hooks if t == hook_type], *args, **kwargs
        )

    @asynccontextmanager
    async def _invoke_context(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        merged = merge_kwargs(self._fixed_kwargs, kwargs, f"tool {self.fn.__name__!r}")
        merged = inject_context_deps(self.fn, merged)
        lock_ctx = self.lock if self.lock is not None else null_lock
        async with lock_ctx:
            _start = datetime.now()
            try:
                await self._run_hooks(ToolHook.BEFORE_INVOKE, *args, **merged)
                yield merged
            finally:
                self.metadata.start_time = _start
                self.metadata.end_time = datetime.now()

    @overload
    def before_invoke(
        self,
        fn: BeforeInvokeFn[P],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def before_invoke(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[BeforeInvokeFn[P]], Hook]: ...

    def before_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before the tool runs.

        Parameters
        ----------
        fn : async (*args, **kwargs) -> None
            Receives the same positional and keyword arguments as the
            tool itself. May also declare ``ContextQueue`` and/or
            ``ContextPool`` parameters **before** the tool parameters;
            they are injected automatically at runtime.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ToolHook.BEFORE_INVOKE, self.hooks, fn, lock, fixed_kwargs, as_tuple=True
        )

    def after_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the tool completes.

        See ``Tool.after_invoke`` and ``AsyncGenTool.after_invoke`` for
        the concrete argument each subclass passes.

        Parameters
        ----------
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ToolHook.AFTER_INVOKE, self.hooks, fn, lock, fixed_kwargs, as_tuple=True
        )


class Tool(Generic[P, R], _BaseTool[P]):
    """Typed wrapper for a coroutine tool."""

    fn: Callable[P, Awaitable[R]]

    @overload
    def after_invoke(
        self,
        fn: AfterInvokeFn[R],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def after_invoke(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[AfterInvokeFn[R]], Hook]: ...

    def after_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the tool returns.

        Parameters
        ----------
        fn : async (result: R, ...) -> None
            Receives the tool's return value. May also request
            ``ContextQueue`` and/or ``ContextPool``; they are injected when
            present in the signature.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return super().after_invoke(fn, lock=lock, **fixed_kwargs)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._invoke_context(args, kwargs) as merged:
            result = await self.fn(*args, **merged)
        await self._run_hooks(ToolHook.AFTER_INVOKE, result)
        return result


class AsyncGenTool(Generic[P, Y], _BaseTool[P]):
    """Typed wrapper for an async-generator tool."""

    fn: Callable[P, AsyncIterator[Y]]

    @overload
    def after_invoke(
        self,
        fn: AfterInvokeGenFn[Y],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def after_invoke(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[AfterInvokeGenFn[Y]], Hook]: ...

    def after_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the generator is exhausted.

        Parameters
        ----------
        fn : async (values: list[Y], ...) -> None
            Receives a list of all yielded items. May also request
            ``ContextQueue`` and/or ``ContextPool``; they are injected when
            present in the signature.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return super().after_invoke(fn, lock=lock, **fixed_kwargs)

    @overload
    def on_yield(
        self,
        fn: OnYieldFn[Y],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def on_yield(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[OnYieldFn[Y]], Hook]: ...

    def on_yield(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires for each yielded value.

        Parameters
        ----------
        fn : async (value: Y, ...) -> None
            Receives the individual yielded value. May also request
            ``ContextQueue`` and/or ``ContextPool``; they are injected when
            present in the signature.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ToolHook.ON_YIELD, self.hooks, fn, lock, fixed_kwargs, as_tuple=True
        )

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AsyncIterator[Y]:
        aggregated: list[Y] = []
        async with self._invoke_context(args, kwargs) as merged:
            async for value in self.fn(*args, **merged):
                await self._run_hooks(ToolHook.ON_YIELD, value)
                aggregated.append(value)
                yield value
        await self._run_hooks(ToolHook.AFTER_INVOKE, aggregated)


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
