import functools
import inspect
from typing import Any, Callable, NamedTuple, Protocol, TypeVar, cast

from app.enums import ToolType
from app.registry import ToolRegistry

T = TypeVar("T", bound=Any)


class ToolMetadata(NamedTuple):
    name: str
    description: str
    type: ToolType
    approval: bool

    def dict(self) -> dict[str, Any]:
        return self._asdict()


class Tool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def tool(
    func: Callable[..., T] | None = None,
    *,
    type: ToolType = ToolType.ACTION,
    approval: bool = False,
) -> Callable[..., T]:
    """
    Tools contain instructions for how something should be done.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if not (inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)):
            raise TypeError(
                "Tool must be async (coroutine or async generator function)."
            )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.metadata = ToolMetadata(fn.__name__, fn.__doc__, type, approval)
        wrapper.fn = fn
        ToolRegistry.register(cast(Tool, wrapper))
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
