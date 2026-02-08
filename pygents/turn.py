import asyncio
import inspect
import time
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, TypeVar
from uuid import uuid4

from pygents.errors import SafeExecutionError, TurnTimeoutError, WrongRunMethodError
from pygents.hooks import Hook, TurnHook, run_hooks
from pygents.registry import HookRegistry, ToolRegistry
from pygents.tool import Tool
from pygents.utils import eval_kwargs, safe_execution

T = TypeVar("T")


class StopReason(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


class _NullLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, *args: Any) -> None:
        pass


_null_lock = _NullLock()


class Turn[T]:
    """
    A Turn represents a single conceptual unit of work. It describes what should happen - but not how it should happen.
    """

    uuid: str
    tool: Tool | None = None
    kwargs: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
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
        kwargs: dict[str, Any] = {},
        metadata: dict[str, Any] | None = None,
        timeout: int = 60,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        stop_reason: StopReason | None = None,
        uuid: str | None = None,
    ):
        self.uuid = uuid if uuid is not None else str(uuid4())
        if isinstance(tool, str):
            resolved = ToolRegistry.get(tool)
        else:
            resolved = ToolRegistry.get(tool.__name__)
        self.tool = resolved
        self.kwargs = kwargs
        self.metadata = metadata if metadata is not None else {}
        self.timeout = timeout
        self.start_time = start_time
        self.end_time = end_time
        self.stop_reason = stop_reason
        self._is_running = False
        self.hooks: dict[TurnHook, list] = {}

    async def _run_hooks(self, name: TurnHook, *args: Any, **kwargs: Any) -> None:
        await run_hooks(self.hooks.get(name, []), *args, **kwargs)

    def add_hook(self, hook_type: TurnHook, hook: Hook, name: str | None = None) -> None:
        """
        Add a hook for the given hook type and register it in HookRegistry.
        """
        HookRegistry.register(hook, name)
        self.hooks.setdefault(hook_type, []).append(hook)

    @safe_execution
    async def returning(self) -> T:
        """
        Run the Turn and return a single result. Use yielding() for async generator tools.
        """
        lock_ctx = self.tool.lock if self.tool.lock is not None else _null_lock
        async with lock_ctx:
            try:
                self._is_running = True
                self.start_time = datetime.now()
                await self._run_hooks(TurnHook.BEFORE_RUN, self)
                if inspect.isasyncgenfunction(self.tool.fn):
                    raise WrongRunMethodError(
                        "Tool is async generator; use yielding() instead."
                    )
                runtime_kwargs = eval_kwargs(self.kwargs)
                self.output = await asyncio.wait_for(
                    self.tool(**runtime_kwargs), timeout=self.timeout
                )
                self.stop_reason = StopReason.COMPLETED
                await self._run_hooks(TurnHook.AFTER_RUN, self)
                return self.output
            except (asyncio.TimeoutError, TimeoutError):
                self.stop_reason = StopReason.TIMEOUT
                await self._run_hooks(TurnHook.ON_TIMEOUT, self)
                raise TurnTimeoutError(
                    f"Turn timed out after {self.timeout}s"
                ) from None
            except Exception as e:
                self.stop_reason = StopReason.ERROR
                await self._run_hooks(TurnHook.ON_ERROR, self, e)
                raise
            finally:
                self.end_time = datetime.now()
                self._is_running = False

    @safe_execution
    async def yielding(self) -> AsyncIterator[T]:
        """
        Run the Turn and yield each result as it is produced. Tools are async generators.
        """
        lock_ctx = self.tool.lock if self.tool.lock is not None else _null_lock
        async with lock_ctx:
            try:
                self._is_running = True
                self.start_time = datetime.now()
                await self._run_hooks(TurnHook.BEFORE_RUN, self)
                if not inspect.isasyncgenfunction(self.tool.fn):
                    raise WrongRunMethodError(
                        "Tool is not an async generator; use returning() for single value."
                    )
                runtime_kwargs = eval_kwargs(self.kwargs)
                queue: asyncio.Queue[Any] = asyncio.Queue()

                async def produce() -> None:
                    try:
                        async for value in self.tool(**runtime_kwargs):
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
                            await self._run_hooks(TurnHook.ON_TIMEOUT, self)
                            raise TurnTimeoutError(
                                f"Turn timed out after {self.timeout}s"
                            ) from None
                        item = await asyncio.wait_for(queue.get(), timeout=remaining)
                        if item is None:
                            break
                        aggregated.append(item)
                        await self._run_hooks(TurnHook.ON_VALUE, self, item)
                        yield item
                    await producer
                except (asyncio.TimeoutError, TimeoutError):
                    producer.cancel()
                    try:
                        await producer
                    except asyncio.CancelledError:
                        pass
                    self.stop_reason = StopReason.TIMEOUT
                    await self._run_hooks(TurnHook.ON_TIMEOUT, self)
                    raise TurnTimeoutError(
                        f"Turn timed out after {self.timeout}s"
                    ) from None
                self.output = aggregated
                self.stop_reason = StopReason.COMPLETED
                await self._run_hooks(TurnHook.AFTER_RUN, self)
            except TurnTimeoutError:
                raise
            except Exception as e:
                self.stop_reason = StopReason.ERROR
                await self._run_hooks(TurnHook.ON_ERROR, self, e)
                raise
            finally:
                self.end_time = datetime.now()
                self._is_running = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "tool_name": self.tool.metadata.name,
            "kwargs": eval_kwargs(self.kwargs),
            "metadata": self.metadata,
            "timeout": self.timeout,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "output": self.output,
            "hooks": {k.value: [h.__name__ for h in v] for k, v in self.hooks.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn[Any]":
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        stop_reason = data.get("stop_reason")
        turn = cls(
            tool=data["tool_name"],
            kwargs=data.get("kwargs", {}),
            metadata=data.get("metadata", {}),
            timeout=data.get("timeout", 60),
            start_time=datetime.fromisoformat(start_time) if start_time else None,
            end_time=datetime.fromisoformat(end_time) if end_time else None,
            stop_reason=StopReason(stop_reason) if stop_reason else None,
            uuid=data.get("uuid"),
        )
        if "output" in data:
            turn.output = data["output"]
        for hook_type_str, hook_names in data.get("hooks", {}).items():
            hook_type = TurnHook(hook_type_str)
            turn.hooks[hook_type] = [HookRegistry.get(name) for name in hook_names]
        return turn
