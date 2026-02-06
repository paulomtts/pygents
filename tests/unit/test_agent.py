import asyncio

import pytest

from pygents.errors import (
    CompletionCheckReturnError,
    SafeExecutionError,
    TurnTimeoutError,
    UnregisteredAgentError,
    UnregisteredToolError,
)
from pygents.registry import AgentRegistry
from pygents.tool import tool, ToolType
from pygents.turn import Turn
from pygents.agent import Agent


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


def test_agent_rejects_tool_not_in_registry():
    unregistered = type(
        "FakeTool",
        (),
        {"metadata": type("M", (), {"name": "unregistered_tool"})()},
    )()
    with pytest.raises(UnregisteredToolError, match="not found"):
        Agent("a", "desc", [unregistered])


def test_put_rejects_turn_with_unknown_tool():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", {"a": 1, "b": 2})
    fake_tool = type("FakeTool", (), {"metadata": type("M", (), {"name": "other_tool"})()})()
    object.__setattr__(turn, "tool", fake_tool)
    with pytest.raises(ValueError, match="does not accept tool"):
        asyncio.run(agent.put(turn))


def test_put_then_pop_returns_same_turn():
    AgentRegistry.clear()
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
    AgentRegistry.clear()
    agent = Agent("a", "desc", [is_done_agent])

    async def consume():
        await agent.put(Turn("is_done_agent", {}))
        async for _ in agent.run():
            pass

    asyncio.run(consume())
    assert agent._queue.empty()


def test_is_completion_check_true_raises_when_output_not_bool():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [is_done_agent])
    turn = Turn("is_done_agent", {})

    async def run_then_corrupt():
        await agent.put(turn)
        async for _, _ in agent.run():
            pass
        turn.output = 1

    asyncio.run(run_then_corrupt())
    with pytest.raises(CompletionCheckReturnError, match="must return bool"):
        agent._is_completion_check_true(turn)


def test_run_streams_turn_results():
    AgentRegistry.clear()
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
    AgentRegistry.clear()
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
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent])

    async def consume():
        await agent.put(Turn("slow_tool_agent", {"duration": 2.0}, timeout=1))
        async for _ in agent.run():
            pass

    with pytest.raises(TurnTimeoutError, match="timed out"):
        asyncio.run(consume())


def test_run_reentrant_raises_safe_execution_error():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent, is_done_agent])

    async def main():
        await agent.put(Turn("slow_tool_agent", {"duration": 2.0}))
        await agent.put(Turn("is_done_agent", {}))
        runner = asyncio.create_task(_consume(agent.run()))
        await asyncio.sleep(0.05)
        with pytest.raises(SafeExecutionError, match="running"):
            async for _ in agent.run():
                pass
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass

    async def _consume(gen):
        async for _ in gen:
            pass

    asyncio.run(main())


def test_send_turn_enqueues_on_target_agent():
    AgentRegistry.clear()
    agent_a = Agent("alice", "First", [add_agent, is_done_agent])
    agent_b = Agent("bob", "Second", [add_agent, is_done_agent])

    async def main():
        await agent_a.send_turn("bob", Turn("add_agent", {"a": 10, "b": 20}))
        await agent_a.send_turn("bob", Turn("is_done_agent", {}))
        out = []
        async for t, v in agent_b.run():
            out.append((t, v))
        return out

    items = asyncio.run(main())
    assert len(items) == 2
    assert items[0][1] == 30
    assert items[1][1] is True


def test_send_turn_to_unregistered_agent_raises():
    AgentRegistry.clear()
    agent = Agent("solo", "Only", [add_agent])

    async def main():
        await agent.send_turn("nonexistent", Turn("add_agent", {"a": 1, "b": 2}))

    with pytest.raises(UnregisteredAgentError, match="not found"):
        asyncio.run(main())


def test_agent_to_dict():
    AgentRegistry.clear()
    agent = Agent("serial", "Serializable agent", [add_agent, is_done_agent])
    data = agent.to_dict()
    assert data["name"] == "serial"
    assert data["description"] == "Serializable agent"
    assert data["tool_names"] == ["add_agent", "is_done_agent"]
    assert data["queue"] == []


def test_agent_to_dict_includes_queued_turns():
    AgentRegistry.clear()
    agent = Agent("q", "With queue", [add_agent, is_done_agent])

    async def put_then_serialize():
        await agent.put(Turn("add_agent", {"a": 2, "b": 3}, metadata={"m": 1}))
        await agent.put(Turn("is_done_agent", {}))
        return agent.to_dict()

    data = asyncio.run(put_then_serialize())
    assert len(data["queue"]) == 2
    assert data["queue"][0]["tool_name"] == "add_agent"
    assert data["queue"][0]["kwargs"] == {"a": 2, "b": 3}
    assert data["queue"][0]["metadata"] == {"m": 1}
    assert data["queue"][1]["tool_name"] == "is_done_agent"


def test_agent_from_dict_roundtrip():
    AgentRegistry.clear()
    agent = Agent("roundtrip", "Desc", [add_agent, is_done_agent])

    async def put_run_serialize():
        await agent.put(Turn("add_agent", {"a": 5, "b": 10}))
        await agent.put(Turn("is_done_agent", {}))
        return agent.to_dict()

    data = asyncio.run(put_run_serialize())
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.name == agent.name
    assert restored.description == agent.description
    assert restored._tool_names == agent._tool_names
    assert restored._queue.qsize() == 2

    async def run_restored():
        results = []
        async for t, v in restored.run():
            results.append((t, v))
        return results

    results = asyncio.run(run_restored())
    assert len(results) == 2
    assert results[0][1] == 15
    assert results[1][1] is True


def test_agent_from_dict_empty_queue():
    AgentRegistry.clear()
    agent = Agent("empty", "No turns", [add_agent])
    data = agent.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.name == "empty"
    assert restored._queue.qsize() == 0
