import asyncio
import inspect
from typing import Any, AsyncIterator

from app.enums import ToolType
from app.errors import TurnTimeoutError
from app.tool import Tool
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

    async def pop(self) -> Turn:
        """
        Pop a Turn from the queue.
        """
        return await self._queue.get()

    def put(self, turn: Turn) -> None:
        """
        Put a Turn on the queue.
        """
        if turn.tool is None:
            raise ValueError("Turn has no tool")
        if turn.tool.metadata.name not in self._tool_names:
            raise ValueError(
                f"Agent {self.name!r} does not accept tool {turn.tool.metadata.name!r}"
            )
        self._queue.put_nowait(turn)

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
            turn = await self.pop()
            try:
                if inspect.isasyncgenfunction(turn.tool.fn):
                    async for value in turn.yielding():
                        yield (turn, value)
                else:
                    output = await turn.returning()
                    yield (turn, output)
            except TurnTimeoutError:
                raise
            except Exception:
                raise

            if (
                turn.tool.metadata.type == ToolType.COMPLETION_CHECK
                and turn.output is True
            ):
                break

            if isinstance(turn.output, Turn):
                self.put(turn.output)
