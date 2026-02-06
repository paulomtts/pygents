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
) -> Callable[..., T]:
    """
    Tools contain instructions for how something should be done.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                await run_hooks(wrapper.hooks.get(ToolHook.BEFORE_INVOKE, []), *args, **kwargs)
                async for value in fn(*args, **kwargs):
                    await run_hooks(wrapper.hooks.get(ToolHook.AFTER_INVOKE, []), value)
                    yield value
        else:
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                await run_hooks(wrapper.hooks.get(ToolHook.BEFORE_INVOKE, []), *args, **kwargs)
                result = await fn(*args, **kwargs)
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
