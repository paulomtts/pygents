import asyncio

from pygents.agent import Agent
from pygents.hooks import AgentHook, ToolHook, TurnHook
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn


# --- Tool hooks tests ---


@tool()
async def tool_for_hook_test(x: int) -> int:
    return x * 2


@tool()
async def tool_for_hook_test_gen():
    yield 1
    yield 2
    yield 3


def test_tool_hooks_before_and_after_invoke():
    HookRegistry.clear()
    events = []

    async def before_hook(*args, **kwargs):
        events.append(("before", args, kwargs))

    async def after_hook(result):
        events.append(("after", result))

    @tool(hooks={ToolHook.BEFORE_INVOKE: [before_hook], ToolHook.AFTER_INVOKE: [after_hook]})
    async def hooked_tool(x: int) -> int:
        return x + 10

    turn = Turn("hooked_tool", kwargs={"x": 5})
    result = asyncio.run(turn.returning())

    assert result == 15
    assert len(events) == 2
    assert events[0][0] == "before"
    assert events[0][2] == {"x": 5}
    assert events[1] == ("after", 15)


def test_tool_hooks_async_gen_before_and_after_invoke():
    HookRegistry.clear()
    events = []

    async def before_hook(*args, **kwargs):
        events.append(("before", kwargs))

    async def after_hook(value):
        events.append(("after", value))

    @tool(hooks={ToolHook.BEFORE_INVOKE: [before_hook], ToolHook.AFTER_INVOKE: [after_hook]})
    async def hooked_gen_tool():
        yield "a"
        yield "b"

    turn = Turn("hooked_gen_tool", kwargs={})

    async def collect():
        return [x async for x in turn.yielding()]

    items = asyncio.run(collect())

    assert items == ["a", "b"]
    assert events[0] == ("before", {})
    assert ("after", "a") in events
    assert ("after", "b") in events


def test_tool_hooks_registered_in_hook_registry():
    HookRegistry.clear()

    async def registered_tool_hook():
        pass

    @tool(hooks={ToolHook.BEFORE_INVOKE: [registered_tool_hook]})
    async def tool_with_registered_hook() -> None:
        pass

    retrieved = HookRegistry.get("registered_tool_hook")
    assert retrieved is registered_tool_hook


# --- Turn add_hook and serialization tests ---


def test_turn_add_hook_registers_and_adds():
    HookRegistry.clear()

    async def turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.add_hook(TurnHook.BEFORE_RUN, turn_hook)

    assert turn_hook in turn.hooks[TurnHook.BEFORE_RUN]
    assert HookRegistry.get("turn_hook") is turn_hook


def test_turn_add_hook_with_custom_name():
    HookRegistry.clear()

    async def another_turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.add_hook(TurnHook.AFTER_RUN, another_turn_hook, name="custom_turn_hook")

    assert another_turn_hook in turn.hooks[TurnHook.AFTER_RUN]
    assert HookRegistry.get("custom_turn_hook") is another_turn_hook


def test_turn_to_dict_includes_hooks():
    HookRegistry.clear()

    async def serializable_turn_hook(turn):
        pass

    HookRegistry.register(serializable_turn_hook)

    turn = Turn("tool_for_hook_test", kwargs={"x": 10})
    turn.hooks[TurnHook.BEFORE_RUN] = [serializable_turn_hook]

    data = turn.to_dict()
    assert "hooks" in data
    assert data["hooks"] == {"before_run": ["serializable_turn_hook"]}


def test_turn_from_dict_restores_hooks():
    HookRegistry.clear()

    async def restorable_turn_hook(turn):
        pass

    HookRegistry.register(restorable_turn_hook)

    data = {
        "tool_name": "tool_for_hook_test",
        "kwargs": {"x": 5},
        "hooks": {"before_run": ["restorable_turn_hook"]},
    }

    turn = Turn.from_dict(data)
    assert TurnHook.BEFORE_RUN in turn.hooks
    assert turn.hooks[TurnHook.BEFORE_RUN] == [restorable_turn_hook]


def test_turn_serialization_roundtrip_with_hooks():
    HookRegistry.clear()
    events = []

    async def roundtrip_hook(turn):
        events.append(id(turn))

    HookRegistry.register(roundtrip_hook)

    turn = Turn("tool_for_hook_test", kwargs={"x": 7})
    turn.hooks[TurnHook.BEFORE_RUN] = [roundtrip_hook]

    data = turn.to_dict()
    restored = Turn.from_dict(data)

    assert restored.hooks[TurnHook.BEFORE_RUN] == [roundtrip_hook]

    asyncio.run(restored.returning())
    assert events == [id(restored)]
    assert restored.output == 14


# --- Agent add_hook and serialization tests ---


def test_agent_add_hook_registers_and_adds():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def agent_hook(agent):
        pass

    agent = Agent("hook_agent", "Test agent", [tool_for_hook_test])
    agent.add_hook(AgentHook.BEFORE_TURN, agent_hook)

    assert agent_hook in agent.hooks[AgentHook.BEFORE_TURN]
    assert HookRegistry.get("agent_hook") is agent_hook


def test_agent_add_hook_with_custom_name():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def another_agent_hook(agent):
        pass

    agent = Agent("hook_agent2", "Test agent", [tool_for_hook_test])
    agent.add_hook(AgentHook.AFTER_TURN, another_agent_hook, name="custom_agent_hook")

    assert another_agent_hook in agent.hooks[AgentHook.AFTER_TURN]
    assert HookRegistry.get("custom_agent_hook") is another_agent_hook


def test_agent_to_dict_includes_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def serializable_agent_hook(agent):
        pass

    HookRegistry.register(serializable_agent_hook)

    agent = Agent("serial_hook_agent", "Agent with hooks", [tool_for_hook_test])
    agent.hooks[AgentHook.BEFORE_TURN] = [serializable_agent_hook]

    data = agent.to_dict()
    assert "hooks" in data
    assert data["hooks"] == {"before_turn": ["serializable_agent_hook"]}


def test_agent_from_dict_restores_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def restorable_agent_hook(agent):
        pass

    HookRegistry.register(restorable_agent_hook)

    agent = Agent("restore_hook_agent", "Restorable", [tool_for_hook_test])
    agent.hooks[AgentHook.AFTER_TURN] = [restorable_agent_hook]

    data = agent.to_dict()
    AgentRegistry.clear()

    restored = Agent.from_dict(data)
    assert AgentHook.AFTER_TURN in restored.hooks
    assert restored.hooks[AgentHook.AFTER_TURN] == [restorable_agent_hook]


def test_agent_serialization_roundtrip_with_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    async def agent_roundtrip_hook(agent, turn, value):
        events.append(("value", value))

    HookRegistry.register(agent_roundtrip_hook)

    agent = Agent("roundtrip_hook_agent", "Roundtrip test", [tool_for_hook_test])
    agent.hooks[AgentHook.ON_TURN_VALUE] = [agent_roundtrip_hook]

    async def put_and_serialize():
        await agent.put(Turn("tool_for_hook_test", kwargs={"x": 3}))
        return agent.to_dict()

    data = asyncio.run(put_and_serialize())
    AgentRegistry.clear()

    restored = Agent.from_dict(data)
    assert restored.hooks[AgentHook.ON_TURN_VALUE] == [agent_roundtrip_hook]

    async def run_restored():
        results = []
        async for t, v in restored.run():
            results.append(v)
        return results

    results = asyncio.run(run_restored())
    assert results == [6]
    assert events == [("value", 6)]


def test_agent_multiple_hooks_serialization():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def hook_one(agent):
        pass

    async def hook_two(agent):
        pass

    HookRegistry.register(hook_one)
    HookRegistry.register(hook_two)

    agent = Agent("multi_hook_agent", "Multiple hooks", [tool_for_hook_test])
    agent.hooks[AgentHook.BEFORE_TURN] = [hook_one, hook_two]

    data = agent.to_dict()
    assert data["hooks"]["before_turn"] == ["hook_one", "hook_two"]

    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.hooks[AgentHook.BEFORE_TURN] == [hook_one, hook_two]
