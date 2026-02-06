import asyncio
import functools
import inspect
from enum import Enum
from typing import Any, AsyncIterator, Callable, Coroutine, NamedTuple, Protocol, TypeVar, cast

from app.hooks import ToolHook
from app.registry import ToolRegistry

T = TypeVar("T", bound=Any)


class ToolType(str, Enum):
    REASONING = "reasoning"
    ACTION = "action"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    COMPLETION_CHECK = "completion_check"


class ToolMetadata(NamedTuple):
    name: str
    description: str
    type: ToolType
    approval: bool

    def dict(self) -> dict[str, Any]:
        return self._asdict()


class Tool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., AsyncIterator[Any]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class CompletionCheckTool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Coroutine[Any, Any, bool]]
    lock: asyncio.Lock | None

    def __call__(self, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, bool]: ...


def tool(
    func: Callable[..., T] | None = None,
    *,
    type: ToolType = ToolType.ACTION,
    approval: bool = False,
    lock: bool = False,
) -> Callable[..., T]:
    """
    Tools contain instructions for how something should be done.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )
        if type is ToolType.COMPLETION_CHECK:
            if inspect.isasyncgenfunction(fn):
                raise TypeError(
                    "COMPLETION_CHECK tool must be a coroutine, not an async generator."
                )
            sig = inspect.signature(fn)
            if sig.return_annotation is inspect.Signature.empty or sig.return_annotation is not bool:
                raise TypeError(
                    "COMPLETION_CHECK tool must declare return type bool (e.g. async def fn(...) -> bool)."
                )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.metadata = ToolMetadata(fn.__name__, fn.__doc__, type, approval)
        wrapper.fn = fn
        wrapper.lock = asyncio.Lock() if lock else None
        wrapper.hooks: dict[ToolHook, list] = {}
        ToolRegistry.register(cast(Tool, wrapper))
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
