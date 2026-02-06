import asyncio
import inspect
from typing import Any, AsyncIterator

from app.errors import TurnTimeoutError
from app.hooks import AgentHook, run_hooks
from app.tool import Tool, ToolType
from app.turn import Turn


class Agent:
    """
    An Agent is an orchestrator. It runs an event loop that processes Turns from a queue. It controls its flow via a set of policies.
    Agents stream by default: run() is an async generator that yields (turn, value) for each result as it is produced.
    """

    name: str
    description: str

    def __init__(self, name: str, description: str, tools: list[Tool]):
        self.name = name
        self.description = description
        self.tools = tools
        self._tool_names = {t.metadata.name for t in tools}
        self._queue: asyncio.Queue[Turn] = asyncio.Queue()
        self.hooks: dict[AgentHook, list] = {}

    async def _run_hooks(self, name: AgentHook, *args: Any, **kwargs: Any) -> None:
        await run_hooks(self.hooks.get(name, []), *args, **kwargs)

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

    async def _run_turn(self, turn: Turn) -> Any:
        if inspect.isasyncgenfunction(turn.tool.fn):
            return [x async for x in turn.yielding()]
        return await turn.returning()

    async def run(self) -> AsyncIterator[tuple[Turn, Any]]:
        """
        Run the event loop until a completion-check tool returns True.
        Streams results: yields (turn, value) for each value produced (one per run for single-value tools, one per yield for async-gen tools).
        Propagates TurnTimeoutError and any exception raised by a tool.
        """
        while True:
            await self._run_hooks(AgentHook.BEFORE_TURN, self)
            turn = await self.pop()
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

            if (
                turn.tool.metadata.type == ToolType.COMPLETION_CHECK
                and turn.output is True
            ):
                break

            if isinstance(turn.output, Turn):
                await self.put(turn.output)
