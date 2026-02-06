import asyncio
import inspect
from datetime import datetime
from typing import Any, AsyncIterator, Callable, TypeVar
from uuid import uuid4

from app.enums import StopReason, ToolType
from app.errors import SafeExecutionError, WrongRunMethodError
from app.registry import ToolRegistry
from app.tool import Tool, tool

R = TypeVar("R")
T = TypeVar("T")


def safe_execution(func: Callable[..., R]) -> Callable[..., R]:
    def wrapper(self: "Turn", *args: Any, **kwargs: Any) -> R:
        if not self._is_running:
            return func(self, *args, **kwargs)
        raise SafeExecutionError(
            f"Skipped <{func.__name__}> call because {self} is running."
        )

    return wrapper


class Turn[T]:
    """
    A Turn represents a single conceptual unit of work. It describes what should happen - but not how it should happen.
    """

    # TODO: improve this to actually track timeout & stop reasons
    # TODO: implement lock so that tools manipulating shared state are safe

    uuid: str
    tool: Tool | None = (
        None  # ! Can't be serialized, but we can use tool name to find it
    )
    kwargs: dict[str, Any] = {}
    output: Any | None = None
    timeout: int = 60
    start_time: datetime | None = None
    end_time: datetime | None = None
    stop_reason: StopReason | None = None
    _is_running: bool = False

    def __setattr__(self, name: str, value: Any) -> None:
        mutable_while_running = {
            "_is_running",
            "start_time",
            "end_time",
            "output",
            "stop_reason",
        }
        if name not in mutable_while_running and getattr(self, "_is_running", False):
            raise SafeExecutionError(
                f"Cannot change property '{name}' while the turn is running."
            )
        super().__setattr__(name, value)

    def __init__(
        self,
        tool_name: str,
        kwargs: dict[str, Any] = {},
        timeout: int = 60,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        stop_reason: StopReason | None = None,
    ):
        self.uuid = str(uuid4())
        self.tool = ToolRegistry.get(tool_name)
        self.kwargs = kwargs
        self.timeout = timeout
        self.start_time = start_time
        self.end_time = end_time
        self.stop_reason = stop_reason
        self._is_running = False

    @safe_execution
    async def returning(self) -> T:
        """
        Run the Turn and return a single result. Use yielding() for async generator tools.
        """
        try:
            self._is_running = True
            self.start_time = datetime.now()
            if inspect.isasyncgenfunction(self.tool.fn):
                raise WrongRunMethodError(
                    "Tool is async generator; use yielding() instead."
                )
            result = self.tool(**self.kwargs)
            if not asyncio.iscoroutine(result):
                raise WrongRunMethodError(
                    "Tool must be a coroutine (async def); use returning() for single value."
                )
            self.output = await result
            self.stop_reason = StopReason.COMPLETED
            return self.output
        except Exception:
            self.stop_reason = StopReason.ERROR
            raise
        finally:
            self.end_time = datetime.now()
            self._is_running = False

    @safe_execution
    async def yielding(self) -> AsyncIterator[T]:
        """
        Run the Turn and yield each result as it is produced. Tools are async generators.
        """
        try:
            self._is_running = True
            self.start_time = datetime.now()
            if not inspect.isasyncgenfunction(self.tool.fn):
                raise WrongRunMethodError(
                    "Tool is not an async generator; use returning() for single value."
                )
            result = self.tool(**self.kwargs)
            if not inspect.isasyncgen(result):
                raise WrongRunMethodError(
                    "Tool must be an async generator; use yielding() for streaming."
                )
            aggregated: list[Any] = []
            async for value in result:
                aggregated.append(value)
                yield value
            self.output = aggregated
            self.end_time = datetime.now()
            self.stop_reason = StopReason.COMPLETED
        except Exception:
            self.stop_reason = StopReason.ERROR
            raise
        finally:
            self.end_time = datetime.now()
            self._is_running = False


@tool(type=ToolType.ACTION)
async def add(a: int, b: int) -> int:
    return a + b


turn = Turn[int]("add", {"a": 1, "b": 2})
result = asyncio.run(turn.returning())
