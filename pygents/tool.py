from __future__ import annotations

import asyncio
import functools
import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from types import UnionType
from collections.abc import AsyncGenerator as ABCAsyncGenerator, AsyncIterator as ABCAsyncIterator
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Concatenate,
    Coroutine,
    Generic,
    ParamSpec,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    overload,
)

from pygents.hooks import Hook, ToolHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import (
    build_method_decorator,
    filter_args_to_signature,
    inject_context_deps,
    merge_kwargs,
    null_lock,
)

P = ParamSpec("P")  # tool param spec
R = TypeVar("R")  # tool return type
Y = TypeVar("Y")  # async-gen yield type

P2 = ParamSpec("P2")  # subtool param spec (for overloads)
R2 = TypeVar("R2")  # subtool return type
Y2 = TypeVar("Y2")  # subtool async-gen yield type

HookP = ParamSpec("HookP")  # extra params for Tool.after_invoke hooks
GenHookP = ParamSpec("GenHookP")  # extra params for AsyncGenTool.after_invoke hooks
YieldHookP = ParamSpec("YieldHookP")  # extra params for AsyncGenTool.on_yield hooks
ErrorHookP = ParamSpec("ErrorHookP")  # extra params for on_error hooks


@dataclass
class ToolMetadata:
    """Name, description, schemas, and run timing of a tool."""

    name: str
    description: str | None
    start_time: datetime | None = None
    end_time: datetime | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    def dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


def _python_type_to_schema(tp: Any) -> dict[str, Any]:
    """Best-effort JSON-schema-like mapping for common typing constructs."""

    origin = get_origin(tp)
    args = get_args(tp)

    if tp is Any or tp is object:
        return {}

    if tp is str:
        return {"type": "string"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}

    if origin is list or origin is tuple:
        item_type: Any = Any
        if args:
            # list[T] or tuple[T, ...] -> items use first arg
            item_type = args[0]
        return {"type": "array", "items": _python_type_to_schema(item_type)}

    if origin is dict:
        # Object with free-form properties; structure unknown.
        return {"type": "object"}

    if origin is Coroutine or origin is Awaitable:
        # Coroutine[..., T] / Awaitable[T] -> unwrap T.
        if args:
            return _python_type_to_schema(args[-1])
        return {}

    if origin in (AsyncIterator, AsyncGenerator, ABCAsyncIterator, ABCAsyncGenerator):
        # AsyncIterator[T] / AsyncGenerator[T, ...] -> unwrap T.
        if args:
            return _python_type_to_schema(args[0])
        return {}

    if origin is list or origin is tuple:
        # Already handled above, but keep a defensive branch.
        return {"type": "array"}

    if origin in (Union, UnionType):
        # Optional[T] / Union[T1, T2, ...] -> anyOf without explicit null.
        sub_schemas = [_python_type_to_schema(a) for a in args]
        return {"anyOf": sub_schemas}

    # Pydantic-style models (duck-typed, no direct dependency).
    # Supports both v2 (model_json_schema) and v1 (schema).
    if isinstance(tp, type):
        method = getattr(tp, "model_json_schema", None)
        if callable(method):
            try:
                return method()
            except Exception:
                return {}
        method = getattr(tp, "schema", None)
        if callable(method):
            try:
                return method()
            except Exception:
                return {}

    return {}


def _strip_optional(tp: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) for Optional[T] / Union[T, None]."""

    origin = get_origin(tp)
    args = get_args(tp)
    if origin is None or not args:
        return tp, False

    if origin not in (Union, UnionType):
        return tp, False

    non_none = [a for a in args if a is not type(None)]  # noqa: E721
    if len(non_none) == 1:
        return non_none[0], True
    return tp, any(a is type(None) for a in args)  # noqa: E721


def _build_input_schema(fn: Callable[..., Any]) -> dict[str, Any] | None:
    """Build an object schema describing the tool's parameters."""

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None

    try:
        hints = inspect.get_annotations(fn, eval_str=True)
    except Exception:
        hints = {}

    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        annotated = hints.get(name, Any)
        inner_type, is_optional = _strip_optional(annotated)
        schema = _python_type_to_schema(inner_type if is_optional else annotated)
        properties[name] = schema

        has_default = param.default is not inspect.Parameter.empty
        if not has_default and not is_optional:
            required.append(name)

    if not properties:
        return None

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _unwrap_return_annotation(tp: Any, *, is_async_gen: bool) -> Any:
    """Extract the logical return / yield item type from an annotation."""

    origin = get_origin(tp)
    args = get_args(tp)

    if is_async_gen:
        if origin in (AsyncIterator, AsyncGenerator, ABCAsyncIterator, ABCAsyncGenerator):
            if args:
                return args[0]
            return Any
        return tp

    if origin is Coroutine:
        if args:
            return args[-1]
        return Any

    if origin is Awaitable:
        if args:
            return args[0]
        return Any

    return tp


def _build_output_schema(fn: Callable[..., Any], *, is_async_gen: bool) -> dict[str, Any] | None:
    """Build a schema for the tool's logical output value."""

    try:
        hints = inspect.get_annotations(fn, eval_str=True)
    except Exception:
        return None

    ret = hints.get("return")
    if ret is None:
        return None

    unwrapped = _unwrap_return_annotation(ret, is_async_gen=is_async_gen)
    return _python_type_to_schema(unwrapped)


class BaseTool(Generic[P]):
    """Shared base for Tool and AsyncGenTool."""

    fn: Callable[P, Any]
    metadata: ToolMetadata
    lock: asyncio.Lock | None
    hooks: list[tuple[ToolHook, Any]]
    __name__: str

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
        self._subtools: list[BaseTool[Any]] = []
        self._fixed_kwargs = fixed_kwargs or {}
        self.tags: frozenset[str] = frozenset(tags or [])
        functools.update_wrapper(
            cast(Callable[P, Any], self), fn
        )  # ? REASON: make the instance inherit the function's name, docstring, etc.

    def add_subtool(self, child: BaseTool[Any]) -> None:
        self._subtools.append(child)

    def doc_tree(self) -> dict[str, Any]:
        return {
            "name": self.metadata.name,
            "description": self.metadata.description,
            "subtools": [st.doc_tree() for st in self._subtools],
        }

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

    @overload
    def subtool(
        self,
        fn: Callable[P2, Awaitable[R2]],
        *,
        lock: bool = False,
        tags: list[str] | frozenset[str] | None = None,
        **fixed_kwargs: Any,
    ) -> Tool[P2, R2]: ...

    @overload
    def subtool(
        self,
        fn: Callable[P2, AsyncIterator[Y2]],
        *,
        lock: bool = False,
        tags: list[str] | frozenset[str] | None = None,
        **fixed_kwargs: Any,
    ) -> AsyncGenTool[P2, Y2]: ...

    @overload
    def subtool(
        self,
        fn: None = None,
        *,
        lock: bool = False,
        tags: list[str] | frozenset[str] | None = None,
        **fixed_kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Tool[Any, Any] | AsyncGenTool[Any, Any]]: ...

    def subtool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        lock: bool = False,
        tags: list[str] | frozenset[str] | None = None,
        **fixed_kwargs: Any,
    ) -> (
        Tool[Any, Any]
        | AsyncGenTool[Any, Any]
        | Callable[[Callable[..., Any]], Tool[Any, Any] | AsyncGenTool[Any, Any]]
    ):
        """Register an async function as a subtool of this tool.

        The subtool is registered in ToolRegistry and appears under this tool
        in doc_tree(). Same options as the top-level tool() decorator.

        Parameters
        ----------
        fn : async coroutine or async generator function
            The function to wrap as a subtool.
        lock : bool, optional
            If True, concurrent invocations of the subtool are serialized.
        tags : list[str] | frozenset[str] | None, optional
            Tags for the subtool.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        decorator = _ToolDecorator(lock, tags, fixed_kwargs, register=False)

        def wrap(f: Callable[..., Any]) -> Tool[Any, Any] | AsyncGenTool[Any, Any]:
            instance = decorator(f)
            instance.__name__ = f"{self.__name__}.{instance.metadata.name}"
            ToolRegistry.register(instance)
            self.add_subtool(instance)
            return instance

        if fn is not None:
            return wrap(fn)
        return wrap


class Tool(Generic[P, R], BaseTool[P]):
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
    ) -> Callable[
        [Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]]], Hook
    ]: ...

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        return super().on_error(fn, lock=lock, **fixed_kwargs)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._invoke_context(args, kwargs) as merged:
            bound_args, bound_kwargs = filter_args_to_signature(
                self.fn, args, merged
            )
            lock_ctx = self.lock if self.lock is not None else null_lock
            try:
                async with lock_ctx:
                    result = await self.fn(*bound_args, **bound_kwargs)
            except Exception as exc:
                await self._run_hooks(ToolHook.ON_ERROR, exc=exc)
                raise
        await self._run_hooks(ToolHook.AFTER_INVOKE, result=result)
        return result


class AsyncGenTool(Generic[P, Y], BaseTool[P]):
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
    ) -> Callable[
        [Callable[Concatenate[list[Y], GenHookP], Awaitable[None]]], Hook
    ]: ...

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
    ) -> Callable[
        [Callable[Concatenate[Exception, ErrorHookP], Awaitable[None]]], Hook
    ]: ...

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        return super().on_error(fn, lock=lock, **fixed_kwargs)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AsyncIterator[Y]:
        aggregated: list[Y] = []
        async with self._invoke_context(args, kwargs) as merged:
            bound_args, bound_kwargs = filter_args_to_signature(
                self.fn, args, merged
            )
            lock_ctx = self.lock if self.lock is not None else null_lock
            _errored = False
            try:
                try:
                    async with lock_ctx:
                        async for value in self.fn(*bound_args, **bound_kwargs):
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
        register: bool = True,
    ) -> None:
        self._lock = lock
        self._tags = frozenset(tags or [])
        self._fixed_kwargs = fixed_kwargs
        self._register = register

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
        instance.metadata.input_schema = _build_input_schema(fn)
        instance.metadata.output_schema = _build_output_schema(
            fn, is_async_gen=inspect.isasyncgenfunction(fn)
        )
        if self._register:
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
    to attach lifecycle hooks after decoration. Use @my_tool.subtool() to register
    subtools; use doc_tree() for hierarchical name and description.

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
