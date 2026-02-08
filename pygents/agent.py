import asyncio
import inspect
from typing import Any, AsyncIterator

from pygents.errors import SafeExecutionError, TurnTimeoutError
from pygents.hooks import AgentHook, Hook, run_hooks
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import Tool
from pygents.turn import Turn
from pygents.utils import safe_execution


class Agent:
    """
    An Agent is an orchestrator. It runs an event loop that processes Turns from a queue. It controls its flow via a set of policies.
    Agents stream by default: run() is an async generator that yields (turn, value) for each result as it is produced.
    """

    name: str
    description: str

    _is_running: bool = False

    def __setattr__(self, name: str, value: Any) -> None:
        if name != "_is_running" and getattr(self, "_is_running", False):
            raise SafeExecutionError(
                f"Cannot change property '{name}' while the agent is running."
            )
        super().__setattr__(name, value)

    def __init__(self, name: str, description: str, tools: list[Tool]):
        for t in tools:
            registered = ToolRegistry.get(t.metadata.name)
            if registered is not t:
                raise ValueError(
                    f"Tool {t.metadata.name!r} is registered but not the instance given to this agent."
                )
        self.name = name
        self.description = description
        self.tools = tools
        self._tool_names = {t.metadata.name for t in tools}
        self._queue: asyncio.Queue[Turn] = asyncio.Queue()
        self.hooks = {}
        self._is_running = False
        AgentRegistry.register(self)

    def _queue_snapshot(self) -> list[Turn]:
        items = []
        for _ in range(self._queue.qsize()):
            turn = self._queue.get_nowait()
            items.append(turn)
            self._queue.put_nowait(turn)
        return items

    async def _run_hooks(self, name: AgentHook, *args: Any, **kwargs: Any) -> None:
        await run_hooks(self.hooks.get(name, []), *args, **kwargs)

    def add_hook(self, hook_type: AgentHook, hook: Hook, name: str | None = None) -> None:
        """
        Add a hook for the given hook type and register it in HookRegistry.
        """
        HookRegistry.register(hook, name)
        self.hooks.setdefault(hook_type, []).append(hook)

    async def pop(self) -> Turn:
        """
        Pop a Turn from the queue.
        """
        return await self._queue.get()

    async def put(self, turn: Turn) -> None:
        """
        Put a Turn on the queue.
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
        """
        Enqueue a Turn on another Agent's queue. The target agent must be registered in AgentRegistry.
        """
        target = AgentRegistry.get(agent_name)
        await target.put(turn)

    async def _run_turn(self, turn: Turn) -> Any:
        if inspect.isasyncgenfunction(turn.tool.fn):
            return [x async for x in turn.yielding()]
        return await turn.returning()

    @safe_execution
    async def run(self) -> AsyncIterator[tuple[Turn, Any]]:
        """
        Run the event loop until the queue is empty.
        Streams results: yields (turn, value) for each value produced (one per run for single-value tools, one per yield for async-gen tools).
        Propagates TurnTimeoutError and any exception raised by a tool.
        """
        try:
            self._is_running = True
            while not self._queue.empty():
                await self._run_hooks(AgentHook.BEFORE_TURN, self)
                turn = self._queue.get_nowait()
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
                except TurnTimeoutError:
                    await self._run_hooks(AgentHook.ON_TURN_TIMEOUT, self, turn)
                    raise
                except Exception as e:
                    await self._run_hooks(AgentHook.ON_TURN_ERROR, self, turn, e)
                    raise
                await self._run_hooks(AgentHook.AFTER_TURN, self, turn)

                if isinstance(turn.output, Turn):
                    await self.put(turn.output)
        finally:
            self._is_running = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tool_names": [t.metadata.name for t in self.tools],
            "queue": [t.to_dict() for t in self._queue_snapshot()],
            "hooks": {k.value: [h.__name__ for h in v] for k, v in self.hooks.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Agent":
        tools = [ToolRegistry.get(name) for name in data["tool_names"]]
        agent = cls(data["name"], data["description"], tools)
        for turn_data in data.get("queue", []):
            agent._queue.put_nowait(Turn.from_dict(turn_data))
        for hook_type_str, hook_names in data.get("hooks", {}).items():
            hook_type = AgentHook(hook_type_str)
            agent.hooks[hook_type] = [HookRegistry.get(name) for name in hook_names]
        return agent
