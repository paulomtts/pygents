"""
Integration test: agent run with hooks at every level and shared memory.

Exercises Agent (BEFORE_PUT, AFTER_PUT, BEFORE_TURN, ON_TURN_VALUE, AFTER_TURN),
Turn (BEFORE_RUN, AFTER_RUN), Tool (BEFORE_INVOKE, AFTER_INVOKE), and Memory
(BEFORE_APPEND, AFTER_APPEND) in one flow. The agent holds a Memory instance;
agent hooks append to it so that memory hooks also fire.
"""

import asyncio

from pygents.agent import Agent
from pygents.hooks import AgentHook, MemoryHook, ToolHook, TurnHook, hook
from pygents.memory import Memory
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn


def test_agent_run_with_hooks_and_memory():
    AgentRegistry.clear()
    HookRegistry.clear()
    events = []

    @hook(MemoryHook.BEFORE_APPEND)
    async def memory_before(items):
        events.append("memory_before_append")

    @hook(MemoryHook.AFTER_APPEND)
    async def memory_after(items):
        events.append("memory_after_append")

    memory = Memory(10, hooks=[memory_before, memory_after])

    @hook(ToolHook.BEFORE_INVOKE)
    async def tool_before(*args, **kwargs):
        events.append("tool_before_invoke")

    @hook(ToolHook.AFTER_INVOKE)
    async def tool_after(result):
        events.append("tool_after_invoke")

    @tool(hooks=[tool_before, tool_after])
    async def integration_compute(a: int, b: int) -> int:
        return a + b

    @hook(AgentHook.BEFORE_PUT)
    async def agent_before_put(agent, turn):
        events.append("agent_before_put")

    @hook(AgentHook.AFTER_PUT)
    async def agent_after_put(agent, turn):
        events.append("agent_after_put")

    @hook(AgentHook.BEFORE_TURN)
    async def agent_before_turn(agent):
        events.append("agent_before_turn")
        await agent.memory.append("before_turn")

    @hook(AgentHook.ON_TURN_VALUE)
    async def agent_on_turn_value(agent, turn, value):
        events.append(("agent_on_turn_value", value))

    @hook(AgentHook.AFTER_TURN)
    async def agent_after_turn(agent, turn):
        events.append("agent_after_turn")
        await agent.memory.append("after_turn")

    @hook(TurnHook.BEFORE_RUN)
    async def turn_before_run(turn):
        events.append("turn_before_run")

    @hook(TurnHook.AFTER_RUN)
    async def turn_after_run(turn):
        events.append("turn_after_run")

    agent = Agent(
        "integration_agent", "Agent with hooks and memory", [integration_compute]
    )
    agent.memory = memory
    agent.hooks.extend(
        [
            agent_before_put,
            agent_after_put,
            agent_before_turn,
            agent_on_turn_value,
            agent_after_turn,
        ]
    )

    turn = Turn("integration_compute", kwargs={"a": 3, "b": 5})
    turn.hooks.extend([turn_before_run, turn_after_run])

    async def run():
        await agent.put(turn)
        results = []
        async for t, value in agent.run():
            results.append((t, value))
        return results

    results = asyncio.run(run())

    assert len(results) == 1
    assert results[0][1] == 8

    assert memory.items == ["before_turn", "after_turn"]

    expected_sequence = [
        "agent_before_put",
        "agent_after_put",
        "agent_before_turn",
        "memory_before_append",
        "memory_after_append",
        "turn_before_run",
        "tool_before_invoke",
        "tool_after_invoke",
        "turn_after_run",
        ("agent_on_turn_value", 8),
        "agent_after_turn",
        "memory_before_append",
        "memory_after_append",
    ]
    assert events == expected_sequence


def test_agent_multi_type_hook_invoked_for_each_event():
    AgentRegistry.clear()
    HookRegistry.clear()
    events = []

    @tool()
    async def simple_tool(x: int) -> int:
        return x + 1

    @hook(
        [
            AgentHook.BEFORE_TURN,
            AgentHook.AFTER_TURN,
            TurnHook.BEFORE_RUN,
            TurnHook.AFTER_RUN,
        ]
    )
    async def multi_type_log(*args, **kwargs):
        events.append(("multi_type", len(args)))

    agent = Agent("multi_hook_agent", "Test", [simple_tool])
    agent.hooks.append(multi_type_log)

    turn = Turn("simple_tool", kwargs={"x": 5}, hooks=[multi_type_log])
    asyncio.run(agent.put(turn))

    async def run():
        return [r async for r in agent.run()]

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0][1] == 6
    assert events == [
        ("multi_type", 1),
        ("multi_type", 1),
        ("multi_type", 1),
        ("multi_type", 2),
    ]
