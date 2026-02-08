import asyncio
import functools
import inspect
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    NamedTuple,
    Protocol,
    TypeVar,
    cast,
)

from pygents.hooks import Hook, ToolHook, run_hooks
from pygents.registry import HookRegistry, ToolRegistry
from pygents.utils import eval_kwargs, log

T = TypeVar("T", bound=Any)


class ToolMetadata(NamedTuple):
    name: str
    description: str

    def dict(self) -> dict[str, Any]:
        return self._asdict()


class Tool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., AsyncIterator[Any]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def tool(
    func: Callable[..., T] | None = None,
    *,
    lock: bool = False,
    hooks: dict[ToolHook, list[Hook]] | None = None,
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

        sig = inspect.signature(fn)
        params = sig.parameters
        has_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        if not has_var_kwargs:
            valid_keys = set(params.keys())
            invalid = set(fixed_kwargs.keys()) - valid_keys
            if invalid:
                raise TypeError(
                    f"Tool {fn.__name__!r} fixed kwargs {sorted(invalid)} are not in "
                    "function signature and function does not accept **kwargs."
                )

        def merge_kwargs(call_kwargs: dict[str, Any]) -> dict[str, Any]:
            evaluated = eval_kwargs(fixed_kwargs)
            for key in call_kwargs:
                if key in evaluated:
                    log.warning(
                        "Fixed kwarg %r is overridden by call-time argument for tool %s.",
                        key,
                        fn.__name__,
                    )
            return {**evaluated, **call_kwargs}

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                merged = merge_kwargs(kwargs)
                await run_hooks(
                    wrapper.hooks.get(ToolHook.BEFORE_INVOKE, []), *args, **merged
                )
                async for value in fn(*args, **merged):
                    await run_hooks(wrapper.hooks.get(ToolHook.AFTER_INVOKE, []), value)
                    yield value
        else:

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                merged = merge_kwargs(kwargs)
                await run_hooks(
                    wrapper.hooks.get(ToolHook.BEFORE_INVOKE, []), *args, **merged
                )
                result = await fn(*args, **merged)
                await run_hooks(wrapper.hooks.get(ToolHook.AFTER_INVOKE, []), result)
                return result

        wrapper.metadata = ToolMetadata(fn.__name__, fn.__doc__)
        wrapper.fn = fn
        wrapper.lock = asyncio.Lock() if lock else None
        wrapper.hooks: dict[ToolHook, list] = {}
        if hooks:
            for hook_type, hook_list in hooks.items():
                for hook in hook_list:
                    HookRegistry.register(hook)
                wrapper.hooks[hook_type] = hook_list
        ToolRegistry.register(cast(Tool, wrapper))
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
