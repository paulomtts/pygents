import asyncio
import inspect
from typing import Any, AsyncIterator

from pygents.context_pool import ContextItem, ContextPool
from pygents.errors import SafeExecutionError, TurnTimeoutError
from pygents.hooks import AgentHook, Hook
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import Tool
from pygents.turn import Turn
from pygents.utils import (
    rebuild_hooks_from_serialization,
    safe_execution,
    serialize_hooks_by_type,
)


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
    tools : list[Tool]
        Tools this agent can run. Each must be registered in ToolRegistry
        and be the same instance given here.
    """

    name: str
    description: str

    _is_running: bool = False
    _current_turn: Turn | None = None
    context_pool: ContextPool

    # -- mutation guard -------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in ("_is_running", "_current_turn") and getattr(
            self, "_is_running", False
        ):
            raise SafeExecutionError(
                f"Cannot change property '{name}' while the agent is running."
            )
        super().__setattr__(name, value)

    def __init__(
        self,
        name: str,
        description: str,
        tools: list[Tool],
        context_pool: ContextPool | None = None,
    ):
        for t in tools:
            registered = ToolRegistry.get(t.metadata.name)
            if registered is not t:
                raise ValueError(
                    f"Tool {t.metadata.name!r} is registered but not the instance given to this agent."
                )
        self.name = name
        self.description = description
        self.tools = tools
        self.hooks: list[Hook] = []

        self._tool_names = {t.metadata.name for t in tools}
        self._queue: asyncio.Queue[Turn] = asyncio.Queue()
        self.context_pool = context_pool if context_pool is not None else ContextPool()

        AgentRegistry.register(self)

    def __repr__(self) -> str:
        tool_names = [t.metadata.name for t in self.tools]
        return f"Agent(name={self.name!r}, tools={tool_names})"

    # -- utils -----------------------------------------------------------------

    async def _run_hooks(self, name: AgentHook, *args: Any, **kwargs: Any) -> None:
        if h := HookRegistry.get_by_type(name, self.hooks):
            await h(*args, **kwargs)

    async def _add_context(self, turn: Turn) -> None:
        if not isinstance(turn.output, ContextItem):
            return
        await self.context_pool.add(turn.output)

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
        if turn.tool.metadata.name not in self._tool_names:
            raise ValueError(
                f"Agent {self.name!r} does not accept tool {turn.tool.metadata.name!r}"
            )
        await self._run_hooks(AgentHook.BEFORE_PUT, self, turn)
        self._queue.put_nowait(turn)
        await self._run_hooks(AgentHook.AFTER_PUT, self, turn)

    async def send_turn(self, agent_name: str, turn: Turn) -> None:
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
        tools: list[Tool] | None = None,
        hooks: list[Hook] | None = ...,  # type: ignore[assignment]
    ) -> "Agent":
        """
        Create a child agent that inherits this agent's configuration.

        The child is fully independent after creation. By default, it
        inherits this agent's hooks; pass hooks=[] or a new list to
        override. Queue and current turn are NOT copied.

        Parameters
        ----------
        name : str
            Name for the child agent (must be unique in AgentRegistry).
        description : str | None
            Description for the child. Defaults to this agent's description.
        tools : list[Tool] | None
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
        child = Agent(name, child_description, child_tools)
        child.hooks = list(child_hooks)
        child.context_pool = self.context_pool.branch()
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
                await self._run_hooks(AgentHook.BEFORE_TURN, self)
                turn = self._current_turn
                self._current_turn = None
                if turn is None:
                    turn = self._queue.get_nowait()
                self._current_turn = turn
                try:
                    if inspect.isasyncgenfunction(turn.tool.fn):
                        async for value in turn.yielding():
                            await self._run_hooks(
                                AgentHook.ON_TURN_VALUE, self, turn, value
                            )
                            yield (turn, value)
                    else:
                        output = await turn.returning()
                        await self._run_hooks(
                            AgentHook.ON_TURN_VALUE, self, turn, output
                        )
                        yield (turn, output)
                    await self._add_context(turn)
                except TurnTimeoutError:
                    await self._run_hooks(AgentHook.ON_TURN_TIMEOUT, self, turn)
                    raise
                except Exception as e:
                    await self._run_hooks(AgentHook.ON_TURN_ERROR, self, turn, e)
                    raise
                await self._run_hooks(AgentHook.AFTER_TURN, self, turn)

                if isinstance(turn.output, Turn):
                    await self.put(turn.output)
                self._current_turn = None
        finally:
            self._is_running = False
            self._current_turn = None

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tool_names": [t.metadata.name for t in self.tools],
            "queue": [t.to_dict() for t in self._queue_snapshot()],
            "current_turn": self._current_turn.to_dict()
            if self._current_turn
            else None,
            "hooks": serialize_hooks_by_type(self.hooks),
            "context_pool": self.context_pool.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Agent":
        tools = [ToolRegistry.get(name) for name in data["tool_names"]]
        agent = cls(data["name"], data["description"], tools)
        for turn_data in data.get("queue", []):
            agent._queue.put_nowait(Turn.from_dict(turn_data))
        if data.get("current_turn") is not None:
            agent._current_turn = Turn.from_dict(data["current_turn"])
        agent.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        agent.context_pool = ContextPool.from_dict(data.get("context_pool", {}))
        return agent
