from __future__ import annotations

import asyncio
import inspect
from typing import Any, AsyncIterator, Sequence

from pygents.context import (
    ContextItem,
    ContextPool,
    ContextQueue,
    _current_context_pool,
    _current_context_queue,
)
from pygents.errors import SafeExecutionError
from pygents.hooks import AgentHook, Hook, TurnHook
from pygents.utils import build_method_decorator
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import AsyncGenTool, Tool
from pygents.turn import Turn
from pygents.utils import (
    rebuild_hooks_from_serialization,
    safe_execution,
    serialize_hooks_by_type,
)


def _tool_registry_keys(tool: Tool[Any, Any] | AsyncGenTool[Any, Any]) -> set[str]:
    keys = {tool.__name__}
    for st in getattr(tool, "_subtools", []):
        keys.update(_tool_registry_keys(st))
    return keys


class Agent:
    """
    Orchestrator that runs an event loop processing Turns from a queue.

    Controls flow via policies and hooks. Streams by default: run() is an
    async generator that yields (turn, value) for each result as it is produced.

    Parameters
    ----------
    name : str
        Agent display name.
    description : str
        Agent description.
    tools : Sequence[Tool | AsyncGenTool]
        Tools this agent can run. Each must be registered in ToolRegistry
        and be the same instance given here.
    """

    name: str
    description: str

    _is_running: bool = False
    _current_turn: Turn | None = None
    context_pool: ContextPool
    context_queue: ContextQueue

    # -- mutation guard -------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in ("_is_running", "_current_turn"):
            _is_running = getattr(self, "_is_running", False)
            _is_paused = (
                (not self._pause_event.is_set())
                if hasattr(self, "_pause_event")
                else False
            )
            if _is_running:
                raise SafeExecutionError(
                    f"Cannot change property '{name}' while the agent is running."
                )
            if _is_paused:
                raise SafeExecutionError(
                    f"Cannot change property '{name}' while the agent is paused."
                )
        super().__setattr__(name, value)

    def __init__(
        self,
        name: str,
        description: str,
        tools: Sequence[Tool | AsyncGenTool],
        context_pool: ContextPool | None = None,
        context_queue: ContextQueue | None = None,
        tags: list[str] | frozenset[str] | None = None,
    ):
        tools_list = list(tools)
        for t in tools_list:
            registered = ToolRegistry.get(t.__name__)
            if registered is not t:
                raise ValueError(
                    f"Tool {t.__name__!r} is registered but not the instance given to this agent."
                )
        self.name = name
        self.description = description
        self.tools = tools_list
        self.tags: frozenset[str] = frozenset(tags or [])
        self.hooks: list[Hook] = []
        self.turn_hooks: list[Hook] = []

        self._tool_names = set()
        for t in tools_list:
            self._tool_names.update(_tool_registry_keys(t))
        self._queue: asyncio.Queue[Turn] = asyncio.Queue()
        self.context_pool = context_pool if context_pool is not None else ContextPool()
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # unpaused by default
        self.context_queue = (
            context_queue if context_queue is not None else ContextQueue(limit=10)
        )

        AgentRegistry.register(self)

    def __repr__(self) -> str:
        tool_names = [t.metadata.name for t in self.tools]
        return f"Agent(name={self.name!r}, tools={tool_names})"

    def __iter__(self):
        return iter(self.turns)

    # -- utils -----------------------------------------------------------------

    @property
    def turns(self) -> list[Turn]:
        """Non-destructive snapshot of the pending turns in the queue."""
        return self._queue_snapshot()

    @property
    def is_paused(self) -> bool:
        """True when the agent will not start a new turn until resumed."""
        return not self._pause_event.is_set()

    def pause(self) -> None:
        """Signal the agent to pause before its next turn.

        Safe to call while running. The current turn completes normally;
        the next is blocked until resume(). While paused, agent properties
        cannot be changed. Idempotent.
        """
        self._pause_event.clear()

    def resume(self) -> None:
        """Release a pause, unblocking the agent and property mutation. Idempotent."""
        self._pause_event.set()

    async def _run_hooks(self, name: AgentHook, *args: Any, **kwargs: Any) -> None:
        await HookRegistry.fire(
            name, HookRegistry.get_by_type(name, self.hooks), *args,
            _source_tags=self.tags, **kwargs
        )

    async def _route_value(self, value: Any) -> None:
        if isinstance(value, ContextItem):
            if value.id is None:
                await self.context_queue.append(value)
            else:
                await self.context_pool.add(value)
        elif isinstance(value, Turn):
            await self.put(value)

    # -- agent-scoped hook decorators (AgentHook → self.hooks) -----------------

    def before_turn(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before the agent consumes the next turn from the queue.

        Parameters
        ----------
        fn : async (agent: Agent) -> None
            Receives the agent instance.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """

        return build_method_decorator(
            AgentHook.BEFORE_TURN, self.hooks, fn, lock, fixed_kwargs
        )

    def after_turn(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after a turn is fully processed and its values routed.

        Parameters
        ----------
        fn : async (agent: Agent, turn: Turn) -> None
            Receives the agent instance and the completed turn.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.AFTER_TURN, self.hooks, fn, lock, fixed_kwargs
        )

    def on_turn_value(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after each produced value is routed.

        Parameters
        ----------
        fn : async (agent: Agent, turn: Turn, value: Any) -> None
            Receives the agent, the turn that produced the value, and
            the value itself.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.ON_TURN_VALUE, self.hooks, fn, lock, fixed_kwargs
        )

    def before_put(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before a turn is enqueued via ``put()``.

        Parameters
        ----------
        fn : async (agent: Agent, turn: Turn) -> None
            Receives the agent instance and the turn about to be
            enqueued.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.BEFORE_PUT, self.hooks, fn, lock, fixed_kwargs
        )

    def after_put(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after a turn is enqueued via ``put()``.

        Parameters
        ----------
        fn : async (agent: Agent, turn: Turn) -> None
            Receives the agent instance and the enqueued turn.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.AFTER_PUT, self.hooks, fn, lock, fixed_kwargs
        )

    def on_pause(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires when the run loop hits a paused gate.

        Parameters
        ----------
        fn : async (agent: Agent) -> None
            Receives the agent instance.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.ON_PAUSE, self.hooks, fn, lock, fixed_kwargs
        )

    def on_resume(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires when the pause gate is released and the loop continues.

        Parameters
        ----------
        fn : async (agent: Agent) -> None
            Receives the agent instance.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            AgentHook.ON_RESUME, self.hooks, fn, lock, fixed_kwargs
        )

    # -- turn-scoped hook decorators (TurnHook → self.turn_hooks) --------------

    def on_error(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Propagated to every turn. Fires on non-timeout exceptions.

        Parameters
        ----------
        fn : async (turn: Turn, exception: Exception) -> None
            Receives the turn instance and the raised exception.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            TurnHook.ON_ERROR, self.turn_hooks, fn, lock, fixed_kwargs
        )

    def on_timeout(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Propagated to every turn. Fires when a turn exceeds its timeout.

        Parameters
        ----------
        fn : async (turn: Turn) -> None
            Receives the turn instance.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            TurnHook.ON_TIMEOUT, self.turn_hooks, fn, lock, fixed_kwargs
        )

    def on_complete(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Propagated to every turn. Always fires after the turn ends.

        Parameters
        ----------
        fn : async (turn: Turn, stop_reason: StopReason | None) -> None
            Receives the turn instance and the reason it stopped (or
            ``None`` if the stop reason has not been set yet).
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            TurnHook.ON_COMPLETE, self.turn_hooks, fn, lock, fixed_kwargs
        )

    # -- queue -----------------------------------------------------------------

    async def put(self, turn: Turn) -> None:
        """Put a Turn on the queue.

        Parameters
        ----------
        turn : Turn
            Turn to enqueue. Must have a tool whose name is in this agent's
            tools.

        Raises
        ------
        ValueError
            If turn has no tool or tool is not accepted by this agent.
        """
        if turn.tool is None:
            raise ValueError("Turn has no tool")
        if turn.tool.__name__ not in self._tool_names:
            raise ValueError(
                f"Agent {self.name!r} does not accept tool {turn.tool.__name__!r}"
            )
        await self._run_hooks(AgentHook.BEFORE_PUT, self, turn)
        self._queue.put_nowait(turn)
        await self._run_hooks(AgentHook.AFTER_PUT, self, turn)

    async def send(self, agent_name: str, turn: Turn) -> None:
        """Enqueue a Turn on another agent's queue.

        Parameters
        ----------
        agent_name : str
            Name of the target agent (must be in AgentRegistry).
        turn : Turn
            Turn to enqueue.
        """
        target = AgentRegistry.get(agent_name)
        await target.put(turn)

    # -- branching -------------------------------------------------------------

    def _queue_snapshot(self) -> list[Turn]:
        items = []
        for _ in range(self._queue.qsize()):
            turn = self._queue.get_nowait()
            items.append(turn)
            self._queue.put_nowait(turn)
        return items

    def branch(
        self,
        name: str,
        description: str | None = None,
        tools: Sequence[Tool | AsyncGenTool] | None = None,
        hooks: list[Hook] | None = ...,  # type: ignore[assignment]
    ) -> Agent:
        """
        Create a child agent that inherits this agent's configuration.

        The child is fully independent after creation. By default, it
        inherits this agent's hooks and turn_hooks; pass hooks=[] or a
        new list to override hooks. The queue is copied to the child;
        current turn is not.

        Parameters
        ----------
        name : str
            Name for the child agent (must be unique in AgentRegistry).
        description : str | None
            Description for the child. Defaults to this agent's description.
        tools : Sequence[Tool | AsyncGenTool] | None
            Tools for the child. Defaults to this agent's tools.
        hooks : list[Hook] | None
            Hooks for the child. Sentinel ``...`` (default) inherits this
            agent's hooks; ``None`` or ``[]`` gives no hooks.
        """
        child_description = description if description is not None else self.description
        child_tools = tools if tools is not None else list(self.tools)
        child_hooks = (
            self.hooks if hooks is ... else (hooks if hooks is not None else [])
        )
        child = Agent(name, child_description, child_tools, tags=self.tags)
        child.hooks = list(child_hooks)
        child.turn_hooks = list(self.turn_hooks)
        child.context_pool = self.context_pool.branch()
        child.context_queue = self.context_queue.branch()
        for turn in self._queue_snapshot():
            child._queue.put_nowait(turn)
        return child

    # -- run -------------------------------------------------------------------

    @safe_execution
    async def run(self) -> AsyncIterator[tuple[Turn, Any]]:
        """Run the event loop until the queue is empty.

        Yields (turn, value) for each value produced (one per run for
        single-value tools, one per yield for async-gen tools). Propagates
        TurnTimeoutError and any exception raised by a tool.
        """
        try:
            self._is_running = True
            while self._current_turn is not None or not self._queue.empty():
                if not self._pause_event.is_set():
                    await self._run_hooks(AgentHook.ON_PAUSE, self)
                    await self._pause_event.wait()
                    await self._run_hooks(AgentHook.ON_RESUME, self)
                await self._run_hooks(AgentHook.BEFORE_TURN, self)
                turn = self._current_turn
                self._current_turn = None
                if turn is None:
                    turn = self._queue.get_nowait()
                self._current_turn = turn
                prev_queue = _current_context_queue.get()
                prev_pool = _current_context_pool.get()
                queue_token = _current_context_queue.set(self.context_queue)
                pool_token = _current_context_pool.set(self.context_pool)
                original_hooks = turn.hooks[:]
                turn.hooks.extend(self.turn_hooks)
                try:
                    if inspect.isasyncgenfunction(turn.tool.fn):
                        async for value in turn.yielding():
                            await self._route_value(value)
                            await self._run_hooks(
                                AgentHook.ON_TURN_VALUE, self, turn, value
                            )
                            if not isinstance(value, (ContextItem, Turn)):
                                yield (turn, value)
                    else:
                        output = await turn.returning()
                        await self._route_value(turn.output)
                        await self._run_hooks(
                            AgentHook.ON_TURN_VALUE, self, turn, output
                        )
                        if not isinstance(output, (ContextItem, Turn)):
                            yield (turn, output)
                finally:
                    turn.hooks = original_hooks
                    try:
                        _current_context_queue.reset(queue_token)
                        _current_context_pool.reset(pool_token)
                    except ValueError:
                        _current_context_queue.set(prev_queue)
                        _current_context_pool.set(prev_pool)
                await self._run_hooks(AgentHook.AFTER_TURN, self, turn)
                self._current_turn = None
        finally:
            self._is_running = False
            self._current_turn = None

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tool_names": [t.__name__ for t in self.tools],
            "queue": [t.to_dict() for t in self._queue_snapshot()],
            "current_turn": self._current_turn.to_dict()
            if self._current_turn
            else None,
            "hooks": serialize_hooks_by_type(self.hooks),
            "turn_hooks": serialize_hooks_by_type(self.turn_hooks),
            "context_pool": self.context_pool.to_dict(),
            "is_paused": self.is_paused,
            "context_queue": self.context_queue.to_dict(),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        tools = [ToolRegistry.get(name) for name in data["tool_names"]]
        agent = cls(data["name"], data["description"], tools, tags=data.get("tags", []))
        for turn_data in data.get("queue", []):
            agent._queue.put_nowait(Turn.from_dict(turn_data))
        if data.get("current_turn") is not None:
            agent._current_turn = Turn.from_dict(data["current_turn"])
        agent.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        agent.turn_hooks = rebuild_hooks_from_serialization(data.get("turn_hooks", {}))
        agent.context_pool = ContextPool.from_dict(data.get("context_pool", {}))
        agent.context_queue = ContextQueue.from_dict(
            data.get("context_queue", {"limit": 10, "items": [], "hooks": {}})
        )
        if data.get("is_paused", False):
            agent.pause()
        return agent
