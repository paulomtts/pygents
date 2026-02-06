import asyncio

import pytest

from app.errors import TurnTimeoutError
from app.tool import tool, ToolType
from app.turn import Turn
from app.agent import Agent


@tool(type=ToolType.ACTION)
async def add_agent(a: int, b: int) -> int:
    return a + b


@tool(type=ToolType.COMPLETION_CHECK)
async def is_done_agent() -> bool:
    return True


@tool(type=ToolType.ACTION)
async def slow_tool_agent(duration: float) -> str:
    await asyncio.sleep(duration)
    return "done"


@tool(type=ToolType.ACTION)
async def stream_agent():
    yield 1
    yield 2
    yield 3


def test_put_rejects_turn_with_unknown_tool():
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", {"a": 1, "b": 2})
    fake_tool = type("FakeTool", (), {"metadata": type("M", (), {"name": "other_tool"})()})()
    object.__setattr__(turn, "tool", fake_tool)
    with pytest.raises(ValueError, match="does not accept tool"):
        asyncio.run(agent.put(turn))


def test_put_then_pop_returns_same_turn():
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", {"a": 1, "b": 2})

    async def pop_and_run():
        await agent.put(turn)
        t = await agent.pop()
        return await agent._run_turn(t)

    result = asyncio.run(pop_and_run())
    assert result == 3
    assert turn.output == 3


def test_run_processes_turn_and_stops_when_completion_check_returns_true():
    agent = Agent("a", "desc", [is_done_agent])

    async def consume():
        await agent.put(Turn("is_done_agent", {}))
        async for _ in agent.run():
            pass

    asyncio.run(consume())
    assert agent._queue.empty()


def test_run_streams_turn_results():
    agent = Agent("a", "desc", [add_agent, is_done_agent])

    async def collect():
        await agent.put(Turn("add_agent", {"a": 1, "b": 2}))
        await agent.put(Turn("is_done_agent", {}))
        items = []
        async for t, v in agent.run():
            items.append((t, v))
        return items

    items = asyncio.run(collect())
    assert len(items) == 2
    assert items[0][1] == 3
    assert items[0][0].output == 3
    assert items[1][1] is True
    assert items[1][0].tool.metadata.type == ToolType.COMPLETION_CHECK


def test_run_streams_yielding_turn_multiple_values():
    agent = Agent("a", "desc", [stream_agent, is_done_agent])

    async def collect():
        await agent.put(Turn("stream_agent", {}))
        await agent.put(Turn("is_done_agent", {}))
        items = []
        async for t, v in agent.run():
            items.append((t, v))
        return items

    items = asyncio.run(collect())
    assert len(items) == 4
    assert [v for _, v in items] == [1, 2, 3, True]
    assert items[0][0].output == [1, 2, 3]


def test_run_propagates_turn_timeout_error():
    agent = Agent("a", "desc", [slow_tool_agent])

    async def consume():
        await agent.put(Turn("slow_tool_agent", {"duration": 2.0}, timeout=1))
        async for _ in agent.run():
            pass

    with pytest.raises(TurnTimeoutError, match="timed out"):
        asyncio.run(consume())
