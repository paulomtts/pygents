"""
Tests for pygents.hooks, driven by the following decision table.

Decision table for pygents/hooks.py
-----------------------------------
HookMetadata:
  M1  Construction: name, description, start_time/end_time default None
  M2  dict() with start_time/end_time None -> None in output
  M3  dict() with start_time/end_time set -> isoformat strings

hook() decorator:
  H1  Decorated callable gets hook_type, metadata (name from __name__, description from __doc__), registered in HookRegistry
  H2  lock=True -> wrapper.lock is asyncio.Lock(); concurrent invocations serialized
  H3  lock=False (default) -> wrapper.lock is None
  H4  fixed_kwargs merged into invocation; call-time kwargs override
  H5  fixed_kwarg key not in signature and no **kwargs -> TypeError
  H6  Wrapper call: start_time set, await fn(*args, **merged), end_time set in finally
  H7  get_by_type(hook_type, [wrapper]) returns wrapper
  H8  Multiple hooks same type: get_by_type returns first match
"""

import asyncio
from datetime import datetime

import pytest

from pygents.agent import Agent
from pygents.hooks import AgentHook, HookMetadata, MemoryHook, ToolHook, TurnHook, hook
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn


# --- Fixtures: tools used by hook integration tests ---------------------------------


@tool()
async def tool_for_hook_test(x: int) -> int:
    return x * 2


@tool()
async def tool_for_hook_test_gen():
    yield 1
    yield 2
    yield 3


# --- HookMetadata (M1, M2, M3) -------------------------------------------------------


def test_hook_metadata_construction():
    meta = HookMetadata("my_hook", "A hook.")
    assert meta.name == "my_hook"
    assert meta.description == "A hook."
    assert meta.start_time is None
    assert meta.end_time is None


def test_hook_metadata_dict_with_none_times():
    meta = HookMetadata("h", None)
    assert meta.dict() == {
        "name": "h",
        "description": None,
        "start_time": None,
        "end_time": None,
    }


def test_hook_metadata_dict_after_run_returns_isoformat_times():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def timed_hook(turn):
        pass

    asyncio.run(timed_hook(None))
    data = timed_hook.metadata.dict()
    assert data["start_time"] is not None
    assert data["end_time"] is not None
    datetime.fromisoformat(data["start_time"])
    datetime.fromisoformat(data["end_time"])


# --- hook() decorator: registration, metadata, type (H1, H7, H8) ---------------------


def test_hook_sets_type_metadata_and_registers():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def decorated_before_run(turn):
        pass

    assert decorated_before_run.hook_type == TurnHook.BEFORE_RUN
    assert decorated_before_run.metadata == HookMetadata("decorated_before_run", None)
    assert HookRegistry.get("decorated_before_run") is decorated_before_run
    assert HookRegistry.get_by_type(TurnHook.BEFORE_RUN, [decorated_before_run]) is decorated_before_run


def test_hook_metadata_includes_docstring():
    HookRegistry.clear()

    @hook(TurnHook.AFTER_RUN)
    async def my_hook(turn):
        """Runs after the turn."""

    assert my_hook.metadata.description == "Runs after the turn."


def test_hook_get_by_type_returns_first_match():
    HookRegistry.clear()

    @hook(AgentHook.AFTER_TURN)
    async def first_after(agent, turn):
        pass

    @hook(AgentHook.AFTER_TURN)
    async def second_after(agent, turn):
        pass

    single = HookRegistry.get_by_type(AgentHook.AFTER_TURN, [first_after, second_after])
    assert single is first_after


def test_hook_memory_hook_type_accepted():
    HookRegistry.clear()

    @hook(MemoryHook.BEFORE_APPEND)
    async def before_append(items, result):
        result.extend(items)

    assert before_append.hook_type == MemoryHook.BEFORE_APPEND
    assert HookRegistry.get("before_append") is before_append


# --- hook() decorator: lock (H2, H3) --------------------------------------------------


def test_hook_lock_default_none():
    HookRegistry.clear()

    @hook(TurnHook.ON_TIMEOUT)
    async def no_lock_hook(turn):
        pass

    assert no_lock_hook.lock is None


def test_hook_lock_true_serializes_invocation():
    HookRegistry.clear()
    AgentRegistry.clear()
    order = []

    @hook(AgentHook.AFTER_PUT, lock=True)
    async def slow_hook(agent, turn):
        order.append("start")
        await asyncio.sleep(0.02)
        order.append("end")

    agent = Agent("lock_test_agent", "Test", [tool_for_hook_test])
    agent.hooks = [slow_hook]
    turn = Turn("tool_for_hook_test", kwargs={})

    async def concurrent_put():
        await asyncio.gather(agent.put(turn), agent.put(turn))

    asyncio.run(concurrent_put())
    assert order == ["start", "end", "start", "end"]


# --- hook() decorator: fixed_kwargs (H4, H5) -----------------------------------------


def test_hook_fixed_kwargs_merged_into_invocation():
    HookRegistry.clear()
    received = []

    @hook(TurnHook.BEFORE_RUN, extra="fixed")
    async def with_fixed(turn, extra):
        received.append(extra)

    asyncio.run(with_fixed(None))
    assert received == ["fixed"]


def test_hook_call_kwargs_override_fixed_kwargs():
    HookRegistry.clear()
    received = []

    @hook(TurnHook.BEFORE_RUN, extra="fixed")
    async def with_fixed(turn, extra):
        received.append(extra)

    asyncio.run(with_fixed(None, extra="override"))
    assert received == ["override"]


def test_hook_fixed_kwarg_not_in_signature_raises():
    HookRegistry.clear()
    with pytest.raises(TypeError, match="fixed kwargs .* are not in function signature"):

        @hook(TurnHook.BEFORE_RUN, unknown=1)
        async def no_such_param(turn):
            pass


def test_hook_fixed_kwargs_allowed_with_kwargs():
    HookRegistry.clear()
    received = []

    @hook(TurnHook.BEFORE_RUN, extra="fixed")
    async def accepts_kwargs(turn, **kwargs):
        received.append(kwargs.get("extra"))

    asyncio.run(accepts_kwargs(None))
    assert received == ["fixed"]


# --- hook() decorator: timing (H6) ----------------------------------------------------


def test_hook_start_time_end_time_set_on_run():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def timed_hook(turn):
        pass

    assert timed_hook.metadata.start_time is None
    assert timed_hook.metadata.end_time is None
    asyncio.run(timed_hook(None))
    assert timed_hook.metadata.start_time is not None
    assert timed_hook.metadata.end_time is not None
    assert timed_hook.metadata.start_time <= timed_hook.metadata.end_time


# --- Tool hooks integration ----------------------------------------------------------


def test_tool_hooks_before_and_after_invoke():
    HookRegistry.clear()
    events = []

    async def before_hook(*args, **kwargs):
        events.append(("before", args, kwargs))

    async def after_hook(result):
        events.append(("after", result))

    before_hook.hook_type = ToolHook.BEFORE_INVOKE  # type: ignore[attr-defined]
    after_hook.hook_type = ToolHook.AFTER_INVOKE  # type: ignore[attr-defined]

    @tool(hooks=[before_hook, after_hook])
    async def hooked_tool(x: int) -> int:
        return x + 10

    turn = Turn("hooked_tool", kwargs={"x": 5})
    result = asyncio.run(turn.returning())
    assert result == 15
    assert events[0][0] == "before"
    assert events[0][2] == {"x": 5}
    assert events[1] == ("after", 15)


def test_tool_hooks_async_gen_before_on_yield_after():
    HookRegistry.clear()
    events = []

    async def before_hook(*args, **kwargs):
        events.append(("before", kwargs))

    async def on_yield_hook(value):
        events.append(("on_yield", value))

    async def after_hook(value):
        events.append(("after", value))

    before_hook.hook_type = ToolHook.BEFORE_INVOKE  # type: ignore[attr-defined]
    on_yield_hook.hook_type = ToolHook.ON_YIELD  # type: ignore[attr-defined]
    after_hook.hook_type = ToolHook.AFTER_INVOKE  # type: ignore[attr-defined]

    @tool(hooks=[before_hook, on_yield_hook, after_hook])
    async def hooked_gen_tool():
        yield "a"
        yield "b"

    turn = Turn("hooked_gen_tool", kwargs={})
    items = _collect_async(turn.yielding())
    assert items == ["a", "b"]
    assert events[0] == ("before", {})
    assert events[1] == ("on_yield", "a")
    assert events[2] == ("on_yield", "b")
    assert events[3] == ("after", "b")


def test_tool_with_decorated_hook_registered_in_registry():
    HookRegistry.clear()

    @hook(ToolHook.BEFORE_INVOKE)
    async def registered_tool_hook():
        pass

    @tool(hooks=[registered_tool_hook])
    async def tool_with_registered_hook() -> None:
        pass

    assert HookRegistry.get("registered_tool_hook") is registered_tool_hook


# --- Turn hooks integration ----------------------------------------------------------


def test_turn_hook_append_and_registered():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.hooks.append(turn_hook)
    assert turn_hook in turn.hooks
    assert HookRegistry.get("turn_hook") is turn_hook


def test_turn_hook_custom_registry_name():
    HookRegistry.clear()

    async def another_turn_hook(turn):
        pass

    another_turn_hook.hook_type = TurnHook.AFTER_RUN  # type: ignore[attr-defined]
    HookRegistry.register(another_turn_hook, "custom_turn_hook", hook_type=TurnHook.AFTER_RUN)
    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.hooks.append(another_turn_hook)
    assert HookRegistry.get("custom_turn_hook") is another_turn_hook


def test_turn_to_dict_includes_hooks():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def serializable_turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 10})
    turn.hooks.append(serializable_turn_hook)
    data = turn.to_dict()
    assert data["hooks"] == {"before_run": ["serializable_turn_hook"]}


def test_turn_from_dict_restores_hooks():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def restorable_turn_hook(turn):
        pass

    data = {
        "tool_name": "tool_for_hook_test",
        "kwargs": {"x": 5},
        "hooks": {"before_run": ["restorable_turn_hook"]},
    }
    turn = Turn.from_dict(data)
    assert len(turn.hooks) == 1
    assert turn.hooks[0] is restorable_turn_hook


def test_turn_roundtrip_with_hooks():
    HookRegistry.clear()
    events = []

    @hook(TurnHook.BEFORE_RUN)
    async def roundtrip_hook(turn):
        events.append(id(turn))

    turn = Turn("tool_for_hook_test", kwargs={"x": 7})
    turn.hooks.append(roundtrip_hook)
    data = turn.to_dict()
    restored = Turn.from_dict(data)
    assert len(restored.hooks) == 1
    assert restored.hooks[0] is roundtrip_hook
    asyncio.run(restored.returning())
    assert events == [id(restored)]
    assert restored.output == 14


# --- Agent hooks integration ---------------------------------------------------------


def test_agent_hook_append_and_registered():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.BEFORE_TURN)
    async def agent_hook(agent):
        pass

    agent = Agent("hook_agent", "Test agent", [tool_for_hook_test])
    agent.hooks.append(agent_hook)
    assert agent_hook in agent.hooks
    assert HookRegistry.get("agent_hook") is agent_hook


def test_agent_hook_custom_registry_name():
    HookRegistry.clear()
    AgentRegistry.clear()

    async def another_agent_hook(agent):
        pass

    another_agent_hook.hook_type = AgentHook.AFTER_TURN  # type: ignore[attr-defined]
    HookRegistry.register(another_agent_hook, "custom_agent_hook", hook_type=AgentHook.AFTER_TURN)
    agent = Agent("hook_agent2", "Test agent", [tool_for_hook_test])
    agent.hooks.append(another_agent_hook)
    assert HookRegistry.get("custom_agent_hook") is another_agent_hook


def test_agent_to_dict_includes_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.BEFORE_TURN)
    async def serializable_agent_hook(agent):
        pass

    agent = Agent("serial_hook_agent", "Agent with hooks", [tool_for_hook_test])
    agent.hooks.append(serializable_agent_hook)
    data = agent.to_dict()
    assert data["hooks"] == {"before_turn": ["serializable_agent_hook"]}


def test_agent_from_dict_restores_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.AFTER_TURN)
    async def restorable_agent_hook(agent):
        pass

    agent = Agent("restore_hook_agent", "Restorable", [tool_for_hook_test])
    agent.hooks.append(restorable_agent_hook)
    data = agent.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert len(restored.hooks) == 1
    assert restored.hooks[0] is restorable_agent_hook


def test_agent_roundtrip_with_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_TURN_VALUE)
    async def agent_roundtrip_hook(agent, turn, value):
        events.append(("value", value))

    agent = Agent("roundtrip_hook_agent", "Roundtrip test", [tool_for_hook_test])
    agent.hooks.append(agent_roundtrip_hook)
    async def put_and_serialize():
        await agent.put(Turn("tool_for_hook_test", kwargs={"x": 3}))
        return agent.to_dict()

    data = asyncio.run(put_and_serialize())
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert len(restored.hooks) == 1
    assert restored.hooks[0] is agent_roundtrip_hook
    async def run_restored():
        return [v async for _, v in restored.run()]
    assert asyncio.run(run_restored()) == [6]
    assert events == [("value", 6)]


def test_agent_multiple_hooks_serialization():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.BEFORE_TURN)
    async def hook_one(agent):
        pass

    @hook(AgentHook.BEFORE_TURN)
    async def hook_two(agent):
        pass

    agent = Agent("multi_hook_agent", "Multiple hooks", [tool_for_hook_test])
    agent.hooks.extend([hook_one, hook_two])
    data = agent.to_dict()
    assert data["hooks"]["before_turn"] == ["hook_one", "hook_two"]
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert len(restored.hooks) == 2
    assert hook_one in restored.hooks
    assert hook_two in restored.hooks


def _collect_async(agen):
    async def run():
        return [x async for x in agen]
    return asyncio.run(run())
