import asyncio
import inspect
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, Iterable, TypeVar

from pygents.context import ContextItem
from pygents.errors import SafeExecutionError, TurnTimeoutError, WrongRunMethodError
from pygents.hooks import Hook, TurnHook
from pygents.registry import HookRegistry, ToolRegistry
from pygents.tool import AsyncGenTool, Tool
from pygents.utils import (
    eval_args,
    eval_kwargs,
    rebuild_hooks_from_serialization,
    safe_execution,
    serialize_hooks_by_type,
)

_QUEUE_SENTINEL = object()
T = TypeVar("T")


class StopReason(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


TurnOutput = T | list[T] | ContextItem[T] | list[ContextItem[T]] | None


@dataclass
class TurnMetadata:
    start_time: datetime | None = None
    end_time: datetime | None = None
    stop_reason: StopReason | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "TurnMetadata":
        return cls(
            start_time=datetime.fromisoformat(data.get("start_time") or "")
            if data.get("start_time")
            else None,
            end_time=datetime.fromisoformat(data.get("end_time") or "")
            if data.get("end_time")
            else None,
            stop_reason=StopReason(data.get("stop_reason"))
            if data.get("stop_reason")
            else None,
        )


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
    hooks : list[Hook] | None
        Optional list of hooks (e.g. TurnHook). Applied during turn execution.
    """

    tool: Tool | AsyncGenTool
    args: list[Any]
    kwargs: dict[str, Any]
    timeout: int = 60

    output: TurnOutput[T]
    metadata: TurnMetadata

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
        timeout: int = 60,
        args: Iterable[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        hooks: list[Hook] | None = None,
    ):
        if isinstance(tool, str):
            resolved = ToolRegistry.get(tool)
        else:
            resolved = ToolRegistry.get(tool.__name__)
        self.tool = resolved
        self.args = list(args) if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self.timeout = timeout
        self.output = None
        self.metadata = TurnMetadata()

        self.hooks: list[Hook] = list(hooks) if hooks else []
        self._is_running = False

    def __repr__(self) -> str:
        tool_name = self.tool.metadata.name if self.tool else None
        return f"Turn(tool={tool_name!r}, timeout={self.timeout}, metadata={self.metadata})"

    # -- hooks -----------------------------------------------------------------

    async def _run_hook(self, hook_type: TurnHook, *args: Any) -> None:
        for h in HookRegistry.get_by_type(hook_type, self.hooks):
            await h(self, *args)

    # -- execution ------------------------------------------------------------

    @safe_execution
    async def returning(self) -> TurnOutput[T]:
        """Run the Turn and return a single result.

        Use yielding() for async generator tools.
        """
        try:
            self._is_running = True
            self.metadata.start_time = datetime.now()
            await self._run_hook(TurnHook.BEFORE_RUN)
            if inspect.isasyncgenfunction(self.tool.fn):
                raise WrongRunMethodError(
                    "Tool is async generator; use yielding() instead."
                )
            runtime_args = eval_args(self.args)
            runtime_kwargs = eval_kwargs(self.kwargs)
            if not isinstance(self.tool, Tool):
                raise WrongRunMethodError(
                    "Tool is not a coroutine; use yielding() instead."
                )
            self.output = await asyncio.wait_for(
                self.tool(*runtime_args, **runtime_kwargs), timeout=self.timeout
            )
            self.metadata.stop_reason = StopReason.COMPLETED
            await self._run_hook(TurnHook.AFTER_RUN)
            return self.output
        except (asyncio.TimeoutError, TimeoutError):
            self.metadata.stop_reason = StopReason.TIMEOUT
            await self._run_hook(TurnHook.ON_TIMEOUT)
            raise TurnTimeoutError(f"Turn timed out after {self.timeout}s") from None
        except Exception as e:
            self.metadata.stop_reason = StopReason.ERROR
            await self._run_hook(TurnHook.ON_ERROR, e)
            raise
        finally:
            self.metadata.end_time = datetime.now()
            self._is_running = False

    @safe_execution
    async def yielding(self) -> AsyncIterator[T]:
        """Run the Turn and yield each result as it is produced.

        For tools that are async generators.
        """
        try:
            self._is_running = True
            self.metadata.start_time = datetime.now()
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
                    if not isinstance(self.tool, AsyncGenTool):
                        raise WrongRunMethodError(
                            "Tool is not an async generator; use returning() for single value."
                        )
                    async for value in self.tool(*runtime_args, **runtime_kwargs):
                        await queue.put(value)
                finally:
                    await queue.put(_QUEUE_SENTINEL)

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
                        self.metadata.stop_reason = StopReason.TIMEOUT
                        await self._run_hook(TurnHook.ON_TIMEOUT)
                        raise TurnTimeoutError(
                            f"Turn timed out after {self.timeout}s"
                        ) from None
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    if item is _QUEUE_SENTINEL:
                        break
                    aggregated.append(item)
                    yield item
                await producer
            except (asyncio.TimeoutError, TimeoutError) as exc:
                if isinstance(exc, TurnTimeoutError):
                    raise
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
                self.metadata.stop_reason = StopReason.TIMEOUT
                await self._run_hook(TurnHook.ON_TIMEOUT)
                raise TurnTimeoutError(
                    f"Turn timed out after {self.timeout}s"
                ) from None
            self.output = aggregated
            self.metadata.stop_reason = StopReason.COMPLETED
            # AFTER_RUN fires before the agent routes the output; use AgentHook.ON_TURN_VALUE
            # if you need to observe post-routing context state.
            await self._run_hook(TurnHook.AFTER_RUN)
        except TurnTimeoutError:
            raise
        except Exception as e:
            self.metadata.stop_reason = StopReason.ERROR
            await self._run_hook(TurnHook.ON_ERROR, e)
            raise
        finally:
            self.metadata.end_time = datetime.now()
            self._is_running = False

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool.metadata.name,
            "args": eval_args(self.args),
            "kwargs": eval_kwargs(self.kwargs),
            "timeout": self.timeout,
            "metadata": self.metadata.to_dict(),
            "output": self.output,
            "hooks": serialize_hooks_by_type(self.hooks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn[Any]":
        turn = cls(
            tool=data["tool_name"],
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
            timeout=data.get("timeout", 60),
        )
        turn.metadata = TurnMetadata.from_dict(data.get("metadata", {}))
        if "output" in data:
            turn.output = data["output"]
        turn.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        return turn
