"""
Integration test: agent run with hooks at every level and shared memory.

Exercises Agent (BEFORE_PUT, AFTER_PUT, BEFORE_TURN, ON_TURN_VALUE,
AFTER_TURN), Turn (BEFORE_RUN, AFTER_RUN, ON_COMPLETE), Tool (BEFORE_INVOKE),
and ContextQueue (BEFORE_APPEND, AFTER_APPEND) in one flow. The agent holds a
ContextQueue instance; agent hooks append to it so that context queue hooks also fire.
"""

import asyncio

from pygents.agent import Agent
from pygents.context import ContextItem, ContextQueue
from pygents.hooks import AgentHook, ContextQueueHook, ToolHook, TurnHook, hook
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn


def test_agent_run_with_hooks_and_memory():
    AgentRegistry.clear()
    HookRegistry.clear()
    events = []

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def memory_before(queue, incoming, current):
        events.append("memory_before_append")

    @hook(ContextQueueHook.AFTER_APPEND)
    async def memory_after(queue, incoming, current):
        events.append("memory_after_append")

    memory = ContextQueue(10)

    @hook(ToolHook.BEFORE_INVOKE)
    async def tool_before(*args, **kwargs):
        events.append("tool_before_invoke")

    @tool()
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
        await agent.memory.append(ContextItem(content="before_turn"))

    @hook(AgentHook.ON_TURN_VALUE)
    async def agent_on_turn_value(agent, turn, value):
        events.append(("agent_on_turn_value", value))

    @hook(TurnHook.ON_COMPLETE)
    async def agent_on_turn_complete(turn, stop_reason):
        events.append("agent_on_turn_complete")

    @hook(AgentHook.AFTER_TURN)
    async def agent_after_turn(agent, turn):
        events.append("agent_after_turn")
        await agent.memory.append(ContextItem(content="after_turn"))

    @hook(TurnHook.BEFORE_RUN)
    async def turn_before_run(turn):
        events.append("turn_before_run")

    @hook(TurnHook.AFTER_RUN)
    async def turn_after_run(turn, output):
        events.append("turn_after_run")

    agent = Agent(
        "integration_agent", "Agent with hooks and memory", [integration_compute]
    )
    agent.memory = memory

    turn = Turn("integration_compute", kwargs={"a": 3, "b": 5})

    async def run():
        await agent.put(turn)
        results = []
        async for t, value in agent.run():
            results.append((t, value))
        return results

    results = asyncio.run(run())

    assert len(results) == 1
    assert results[0][1] == 8

    assert memory.items == [
        ContextItem(content="before_turn"),
        ContextItem(content="after_turn"),
    ]

    expected_sequence = [
        "agent_before_put",
        "agent_after_put",
        "agent_before_turn",
        "memory_before_append",
        "memory_after_append",
        "turn_before_run",
        "tool_before_invoke",
        "turn_after_run",
        "agent_on_turn_complete",
        ("agent_on_turn_value", 8),
        "agent_after_turn",
        "memory_before_append",
        "memory_after_append",
    ]
    assert events == expected_sequence


def test_agent_context_pool_collects_pool_item_outputs():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem

    @tool()
    async def context_tool(key: str, val: int) -> ContextItem:
        return ContextItem(content=val, description=f"Result for {key}", id=key)

    agent = Agent("ctx_agent", "Context pool agent", [context_tool])

    async def run():
        await agent.put(Turn("context_tool", kwargs={"key": "a", "val": 1}))
        await agent.put(Turn("context_tool", kwargs={"key": "b", "val": 2}))
        async for _ in agent.run():
            pass

    asyncio.run(run())

    assert len(agent.context_pool) == 2
    assert agent.context_pool.get("a").content == 1
    assert agent.context_pool.get("b").content == 2


def test_agent_context_queue_collects_items_without_id():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem

    @tool()
    async def queue_tool() -> ContextItem:
        return ContextItem(content=42)

    agent = Agent("queue_agent", "Context queue agent", [queue_tool])

    async def run():
        await agent.put(Turn("queue_tool", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())

    assert len(agent.context_queue) == 1
    assert agent.context_queue.items[0].content == 42
    assert len(agent.context_pool) == 0


def test_agent_context_pool_limit_evicts_oldest():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem, ContextPool

    @tool()
    async def bounded_context_tool(key: str, val: int) -> ContextItem:
        return ContextItem(content=val, description="", id=key)

    agent = Agent("bounded_ctx_agent", "Bounded pool", [bounded_context_tool])
    agent.context_pool = ContextPool(limit=1)

    async def run():
        await agent.put(Turn("bounded_context_tool", kwargs={"key": "x", "val": 10}))
        await agent.put(Turn("bounded_context_tool", kwargs={"key": "y", "val": 20}))
        async for _ in agent.run():
            pass

    asyncio.run(run())

    assert len(agent.context_pool) == 1
    assert agent.context_pool.get("y").content == 20


def test_generator_tool_yielding_context_item_routes_to_queue():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem

    @tool()
    async def gen_queue_tool():
        yield ContextItem(content="hello")
        yield ContextItem(content="world")

    agent = Agent("gen_queue_agent", "desc", [gen_queue_tool])

    async def run():
        await agent.put(Turn("gen_queue_tool", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert len(agent.context_queue) == 2
    assert agent.context_queue.items[0].content == "hello"
    assert agent.context_queue.items[1].content == "world"
    assert len(agent.context_pool) == 0


def test_generator_tool_yielding_context_item_routes_to_pool():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem

    @tool()
    async def gen_pool_tool():
        yield ContextItem(content=1, description="first", id="k1")
        yield ContextItem(content=2, description="second", id="k2")

    agent = Agent("gen_pool_agent", "desc", [gen_pool_tool])

    async def run():
        await agent.put(Turn("gen_pool_tool", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert len(agent.context_pool) == 2
    assert agent.context_pool.get("k1").content == 1
    assert agent.context_pool.get("k2").content == 2
    assert len(agent.context_queue) == 0


def test_generator_tool_yielding_turn_enqueues_and_executes():
    AgentRegistry.clear()
    HookRegistry.clear()

    @tool()
    async def turn_adder(a: int, b: int) -> int:
        return a + b

    @tool()
    async def gen_turn_tool():
        yield Turn("turn_adder", kwargs={"a": 10, "b": 20})

    agent = Agent("gen_turn_agent", "desc", [gen_turn_tool, turn_adder])

    async def run():
        await agent.put(Turn("gen_turn_tool", kwargs={}))
        results = []
        async for t, v in agent.run():
            results.append((t.tool.metadata.name, v))
        return results

    results = asyncio.run(run())
    tool_names = [name for name, _ in results]
    values = [v for _, v in results]
    # Turn yielded by gen_turn_tool is filtered from the stream
    assert "gen_turn_tool" not in tool_names
    assert "turn_adder" in tool_names
    assert 30 in values


def test_generator_tool_yielding_mixed_values_routes_correctly():
    AgentRegistry.clear()
    HookRegistry.clear()
    from pygents.context import ContextItem

    @tool()
    async def mixed_adder(a: int, b: int) -> int:
        return a + b

    @tool()
    async def gen_mixed_tool():
        yield ContextItem(content="ctx")
        yield Turn("mixed_adder", kwargs={"a": 1, "b": 2})
        yield 99  # plain value — no routing

    agent = Agent("gen_mixed_agent", "desc", [gen_mixed_tool, mixed_adder])

    async def run():
        await agent.put(Turn("gen_mixed_tool", kwargs={}))
        results = []
        async for t, v in agent.run():
            results.append((t.tool.metadata.name, v))
        return results

    results = asyncio.run(run())

    # gen_mixed_tool yielded 3 values: ContextItem (filtered), Turn (filtered), 99 (passed through)
    gen_values = [v for name, v in results if name == "gen_mixed_tool"]
    add_values = [v for name, v in results if name == "mixed_adder"]

    assert gen_values == [99]
    assert len(add_values) == 1
    assert add_values[0] == 3

    # ContextItem routed to queue, not duplicated
    assert len(agent.context_queue) == 1
    assert agent.context_queue.items[0].content == "ctx"

    # turn.output for gen_mixed_tool is a list → no double-routing
    assert len(agent.context_pool) == 0


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

    turn = Turn("simple_tool", kwargs={"x": 5})
    asyncio.run(agent.put(turn))

    async def run():
        return [r async for r in agent.run()]

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0][1] == 6
    assert events == [
        ("multi_type", 1),  # BEFORE_TURN(agent)
        ("multi_type", 1),  # BEFORE_RUN(turn)
        ("multi_type", 2),  # AFTER_RUN(turn, output)
        ("multi_type", 2),  # AFTER_TURN(agent, turn)
    ]


def test_send_turn_routes_to_target_agent_and_executes():
    AgentRegistry.clear()
    HookRegistry.clear()

    @tool()
    async def multiply(a: int, b: int) -> int:
        return a * b

    sender = Agent("sender", "Sends turns", [multiply])
    receiver = Agent("receiver", "Receives turns", [multiply])

    async def main():
        await sender.send("receiver", Turn("multiply", kwargs={"a": 3, "b": 4}))
        out = []
        async for _, v in receiver.run():
            out.append(v)
        return out

    results = asyncio.run(main())
    assert results == [12]
    assert sender._queue.empty()


def test_context_accumulates_across_multiple_turns_in_single_run():
    AgentRegistry.clear()
    HookRegistry.clear()

    @tool()
    async def emit_queue_item() -> ContextItem:
        return ContextItem(content="queue_val")

    @tool()
    async def emit_pool_item() -> ContextItem:
        return ContextItem(content="pool_val", description="a result", id="result_key")

    agent = Agent(
        "ctx_agent", "Context accumulation", [emit_queue_item, emit_pool_item]
    )

    async def run():
        await agent.put(Turn("emit_queue_item", kwargs={}))
        await agent.put(Turn("emit_queue_item", kwargs={}))
        await agent.put(Turn("emit_pool_item", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert len(agent.context_queue) == 2
    assert all(item.content == "queue_val" for item in agent.context_queue.items)
    assert len(agent.context_pool) == 1
    assert agent.context_pool.get("result_key").content == "pool_val"
