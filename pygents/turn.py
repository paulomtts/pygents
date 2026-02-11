import asyncio
import inspect
import time
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, Iterable, TypeVar

from pygents.errors import SafeExecutionError, TurnTimeoutError, WrongRunMethodError
from pygents.hooks import Hook, TurnHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.tool import Tool
from pygents.utils import (
    eval_args,
    eval_kwargs,
    hooks_by_type_for_serialization,
    safe_execution,
)

T = TypeVar("T")


class StopReason(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


class Turn[T]:
    """
    Single conceptual unit of work: what should happen, not how.

    Parameters
    ----------
    tool : str | Callable
        Tool name (looked up in ToolRegistry) or callable (by __name__).
    args : Iterable[Any] | None
        Positional arguments for the tool. Callables are evaluated at run time.
    kwargs : dict[str, Any] | None
        Keyword arguments for the tool. Callables are evaluated at run time.
    timeout : int
        Max seconds for the turn to run. Default 60.
    metadata : dict[str, Any] | None
        Optional metadata.
    """

    tool: Tool | None = None
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    output: Any | None = None
    timeout: int = 60
    start_time: datetime | None = None
    end_time: datetime | None = None
    stop_reason: StopReason | None = None

    _is_running: bool = False

    # -- mutation guard -------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        mutable_while_running = {
            "_is_running",
            "start_time",
            "end_time",
            "output",
            "stop_reason",
            "metadata",
        }
        if name not in mutable_while_running and getattr(self, "_is_running", False):
            raise SafeExecutionError(
                f"Cannot change property '{name}' while the turn is running."
            )
        super().__setattr__(name, value)

    def __init__(
        self,
        tool: str | Callable,
        args: Iterable[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        timeout: int = 60,
        metadata: dict[str, Any] | None = None,
    ):
        if isinstance(tool, str):
            resolved = ToolRegistry.get(tool)
        else:
            resolved = ToolRegistry.get(tool.__name__)
        self.tool = resolved
        self.args = list(args) if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self.metadata = metadata if metadata is not None else {}
        self.timeout = timeout
        self.start_time = None
        self.end_time = None
        self.stop_reason = None
        self._is_running = False
        self.hooks: list[Hook] = []

    def __repr__(self) -> str:
        tool_name = self.tool.metadata.name if self.tool else None
        return f"Turn(tool={tool_name!r}, timeout={self.timeout})"

    # -- hooks -----------------------------------------------------------------

    async def _run_hook(self, hook_type: TurnHook, *args: Any) -> None:
        if h := HookRegistry.get_by_type(hook_type, self.hooks):
            await h(self, *args)

    # -- execution ------------------------------------------------------------

    @safe_execution
    async def returning(self) -> T:
        """Run the Turn and return a single result.

        Use yielding() for async generator tools.
        """
        try:
            self._is_running = True
            self.start_time = datetime.now()
            await self._run_hook(TurnHook.BEFORE_RUN)
            if inspect.isasyncgenfunction(self.tool.fn):
                raise WrongRunMethodError(
                    "Tool is async generator; use yielding() instead."
                )
            runtime_args = eval_args(self.args)
            runtime_kwargs = eval_kwargs(self.kwargs)
            self.output = await asyncio.wait_for(
                self.tool(*runtime_args, **runtime_kwargs), timeout=self.timeout
            )
            self.stop_reason = StopReason.COMPLETED
            await self._run_hook(TurnHook.AFTER_RUN)
            return self.output
        except (asyncio.TimeoutError, TimeoutError):
            self.stop_reason = StopReason.TIMEOUT
            await self._run_hook(TurnHook.ON_TIMEOUT)
            raise TurnTimeoutError(
                f"Turn timed out after {self.timeout}s"
            ) from None
        except Exception as e:
            self.stop_reason = StopReason.ERROR
            await self._run_hook(TurnHook.ON_ERROR, e)
            raise
        finally:
            self.end_time = datetime.now()
            self._is_running = False

    @safe_execution
    async def yielding(self) -> AsyncIterator[T]:
        """Run the Turn and yield each result as it is produced.

        For tools that are async generators.
        """
        try:
            self._is_running = True
            self.start_time = datetime.now()
            await self._run_hook(TurnHook.BEFORE_RUN)
            if not inspect.isasyncgenfunction(self.tool.fn):
                raise WrongRunMethodError(
                    "Tool is not an async generator; use returning() for single value."
                )
            runtime_args = eval_args(self.args)
            runtime_kwargs = eval_kwargs(self.kwargs)
            queue: asyncio.Queue[Any] = asyncio.Queue()

            async def produce() -> None:
                try:
                    async for value in self.tool(*runtime_args, **runtime_kwargs):
                        await queue.put(value)
                finally:
                    await queue.put(None)

            producer = asyncio.create_task(produce())
            deadline = time.monotonic() + self.timeout
            aggregated: list[Any] = []
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        producer.cancel()
                        try:
                            await producer
                        except asyncio.CancelledError:
                            pass
                        self.stop_reason = StopReason.TIMEOUT
                        await self._run_hook(TurnHook.ON_TIMEOUT)
                        raise TurnTimeoutError(
                            f"Turn timed out after {self.timeout}s"
                        ) from None
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    if item is None:
                        break
                    aggregated.append(item)
                    await self._run_hook(TurnHook.ON_VALUE, item)
                    yield item
                await producer
            except (asyncio.TimeoutError, TimeoutError):
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
                self.stop_reason = StopReason.TIMEOUT
                await self._run_hook(TurnHook.ON_TIMEOUT)
                raise TurnTimeoutError(
                    f"Turn timed out after {self.timeout}s"
                ) from None
            self.output = aggregated
            self.stop_reason = StopReason.COMPLETED
            await self._run_hook(TurnHook.AFTER_RUN)
        except TurnTimeoutError:
            raise
        except Exception as e:
            self.stop_reason = StopReason.ERROR
            await self._run_hook(TurnHook.ON_ERROR, e)
            raise
        finally:
            self.end_time = datetime.now()
            self._is_running = False

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool.metadata.name,
            "args": eval_args(self.args),
            "kwargs": eval_kwargs(self.kwargs),
            "metadata": self.metadata,
            "timeout": self.timeout,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "output": self.output,
            "hooks": hooks_by_type_for_serialization(self.hooks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn[Any]":
        turn = cls(
            tool=data["tool_name"],
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
            timeout=data.get("timeout", 60),
            metadata=data.get("metadata", {}),
        )
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        stop_reason = data.get("stop_reason")
        turn.start_time = datetime.fromisoformat(start_time) if start_time else None
        turn.end_time = datetime.fromisoformat(end_time) if end_time else None
        turn.stop_reason = StopReason(stop_reason) if stop_reason else None
        if "output" in data:
            turn.output = data["output"]
        for _type_str, hook_names in data.get("hooks", {}).items():
            for hname in hook_names:
                turn.hooks.append(HookRegistry.get(hname))
        return turn
