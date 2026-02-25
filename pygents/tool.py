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

from pygents.hooks import Hook, ToolHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import (
    build_method_decorator,
    inject_context_deps,
    merge_kwargs,
    null_lock,
)

P = ParamSpec("P")           # tool param spec
R = TypeVar("R")             # tool return type
Y = TypeVar("Y")             # async-gen yield type

HookP = ParamSpec("HookP")         # extra params for Tool.after_invoke hooks
GenHookP = ParamSpec("GenHookP")   # extra params for AsyncGenTool.after_invoke hooks
YieldHookP = ParamSpec("YieldHookP")  # extra params for AsyncGenTool.on_yield hooks
ErrorHookP = ParamSpec("ErrorHookP")  # extra params for on_error hooks


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
        tags: list[str] | frozenset[str] | None = None,
    ) -> None:
        self.fn = fn
        self.metadata = ToolMetadata(fn.__name__, fn.__doc__)
        self.lock = asyncio.Lock() if lock else None
        self.hooks = []
        self._fixed_kwargs = fixed_kwargs or {}
        self.tags: frozenset[str] = frozenset(tags or [])
        functools.update_wrapper(
            cast(Callable[P, Any], self), fn
        )  # ? REASON: make the instance inherit the function's name, docstring, etc.

    async def _run_hooks(self, hook_type: ToolHook, *args: Any, **kwargs: Any) -> None:
        await HookRegistry.fire(
            hook_type,
            [h for t, h in self.hooks if t == hook_type],
            *args,
            _source_tags=self.tags,
            **kwargs,
        )

    @asynccontextmanager
    async def _invoke_context(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        merged = merge_kwargs(self._fixed_kwargs, kwargs, f"tool {self.fn.__name__!r}")
        merged = inject_context_deps(self.fn, merged)
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
        fn: Callable[P, Awaitable[None]],
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
    ) -> Callable[[Callable[P, Awaitable[None]]], Hook]: ...

    def before_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before the tool runs.

        Parameters
        ----------
        fn : async (*args, **kwargs) -> None
            Receives the same positional and keyword arguments as the
            tool itself. Use ``get_context_queue()`` / ``get_context_pool()``
            inside the body to access context.
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

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires if the tool raises an exception. Receives exc as first keyword arg.

        Parameters
        ----------
        fn : async (exc: Exception, **extra_kwargs) -> None
            Must accept at least ``exc`` as a keyword argument.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ToolHook.ON_ERROR, self.hooks, fn, lock, fixed_kwargs, as_tuple=True
        )


class Tool(Generic[P, R], _BaseTool[P]):
    """Typed wrapper for a coroutine tool."""

    fn: Callable[P, Awaitable[R]]

    @overload
    def after_invoke(
        self,
        fn: Callable[Concatenate[R, HookP], Awaitable[None]],
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
    ) -> Callable[[Callable[Concatenate[R, HookP], Awaitable[None]]], Hook]: ...

    def after_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the tool returns.

        Parameters
        ----------
        fn : async (result: R, *extra_args: Any, **extra_kwargs: Any) -> None
            Must accept at least the tool's return value (``result: R``)
            as its first parameter. Additional positional and keyword
            parameters are allowed; the framework will only guarantee
            to pass the result (and any fixed kwargs you configure).
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return super().after_invoke(fn, lock=lock, **fixed_kwargs)

    @overload
    def on_error(
        self,
        fn: Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def on_error(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]]], Hook]: ...

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        return super().on_error(fn, lock=lock, **fixed_kwargs)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._invoke_context(args, kwargs) as merged:
            lock_ctx = self.lock if self.lock is not None else null_lock
            try:
                async with lock_ctx:
                    result = await self.fn(*args, **merged)
            except Exception as exc:
                await self._run_hooks(ToolHook.ON_ERROR, exc=exc)
                raise
        await self._run_hooks(ToolHook.AFTER_INVOKE, result=result)
        return result


class AsyncGenTool(Generic[P, Y], _BaseTool[P]):
    """Typed wrapper for an async-generator tool."""

    fn: Callable[P, AsyncIterator[Y]]

    @overload
    def after_invoke(
        self,
        fn: Callable[Concatenate[list[Y], GenHookP], Awaitable[None]],
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
    ) -> Callable[[Callable[Concatenate[list[Y], GenHookP], Awaitable[None]]], Hook]: ...

    def after_invoke(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the generator is exhausted.

        Parameters
        ----------
        fn : async (values: list[Y], *extra_args: Any, **extra_kwargs: Any) -> None
            Must accept at least a list of yielded values (``values:
            list[Y]``) as its first parameter. Additional positional and
            keyword parameters are allowed.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return super().after_invoke(fn, lock=lock, **fixed_kwargs)

    @overload
    def on_yield(
        self,
        fn: Callable[Concatenate[Y, YieldHookP], Awaitable[None]],
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
    ) -> Callable[[Callable[Concatenate[Y, YieldHookP], Awaitable[None]]], Hook]: ...

    def on_yield(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires for each yielded value.

        Parameters
        ----------
        fn : async (value: Y, *extra_args: Any, **extra_kwargs: Any) -> None
            Must accept at least the yielded value (``value: Y``) as its
            first parameter. Additional positional and keyword parameters
            are allowed.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ToolHook.ON_YIELD, self.hooks, fn, lock, fixed_kwargs, as_tuple=True
        )

    @overload
    def on_error(
        self,
        fn: Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]],
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Hook: ...

    @overload
    def on_error(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> Callable[[Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]]], Hook]: ...

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        return super().on_error(fn, lock=lock, **fixed_kwargs)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AsyncIterator[Y]:
        aggregated: list[Y] = []
        async with self._invoke_context(args, kwargs) as merged:
            lock_ctx = self.lock if self.lock is not None else null_lock
            _errored = False
            try:
                try:
                    async with lock_ctx:
                        async for value in self.fn(*args, **merged):
                            await self._run_hooks(ToolHook.ON_YIELD, value)
                            aggregated.append(value)
                            yield value
                except Exception as exc:
                    _errored = True
                    await self._run_hooks(ToolHook.ON_ERROR, exc=exc)
                    raise
            finally:
                if not _errored:
                    await self._run_hooks(ToolHook.AFTER_INVOKE, aggregated)


class _ToolDecorator:
    def __init__(
        self,
        lock: bool,
        tags: list[str] | frozenset[str] | None,
        fixed_kwargs: dict[str, Any],
    ) -> None:
        self._lock = lock
        self._tags = frozenset(tags or [])
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
        instance = ToolClass(
            fn, lock=self._lock, fixed_kwargs=self._fixed_kwargs, tags=self._tags
        )
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
    tags: list[str] | frozenset[str] | None = ...,
    **fixed_kwargs: Any,
) -> _ToolDecorator: ...


def tool(
    func: Callable[..., Any] | None = None,
    *,
    lock: bool = False,
    tags: list[str] | frozenset[str] | None = None,
    **fixed_kwargs: Any,
) -> Any:
    """
    Tools contain instructions for how something should be done.

    Any keyword arguments passed to the decorator (other than lock and tags)
    are merged into every invocation; call-time kwargs override these.

    Use the method decorators @my_tool.before_invoke / .on_yield / .after_invoke
    to attach lifecycle hooks after decoration.

    Parameters
    ----------
    lock : bool, optional
        If True, concurrent invocations of this tool are serialized with an
        asyncio.Lock. Default False.
    tags : list[str] | frozenset[str] | None, optional
        Tags for this tool. Used to filter global hooks that specify a ``tags``
        filter via ``@hook(type, tags={...})``. Default None (empty frozenset).
    **fixed_kwargs
        Fixed keyword arguments merged into every invocation. Call-time kwargs
        override these.
    """
    decorator = _ToolDecorator(lock, tags, fixed_kwargs)
    if func is not None:
        return decorator(func)
    return decorator
