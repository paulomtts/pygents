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
  H8  Multiple hooks same type: get_by_type returns all matches in order
"""

import asyncio
from datetime import datetime

import pytest

from pygents.agent import Agent
from pygents.context import ContextItem, ContextPool, ContextQueue
from pygents.hooks import (
    AgentHook,
    ContextPoolHook,
    ContextQueueHook,
    HookMetadata,
    ToolHook,
    TurnHook,
    hook,
)
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@tool()
async def tool_for_hook_test(x: int) -> int:
    return x * 2


@tool()
async def tool_for_hook_test_gen():
    yield 1
    yield 2
    yield 3


# ---------------------------------------------------------------------------
# M1–M3 – HookMetadata
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# H1, H7–H8 – hook() decorator: registration, metadata, type
# ---------------------------------------------------------------------------


def test_hook_sets_type_metadata_and_registers():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def decorated_before_run(turn):
        pass

    assert decorated_before_run.type == TurnHook.BEFORE_RUN
    assert decorated_before_run.metadata == HookMetadata("decorated_before_run", None)
    assert HookRegistry.get("decorated_before_run") is decorated_before_run
    assert HookRegistry.get_by_type(TurnHook.BEFORE_RUN, [decorated_before_run]) == [
        decorated_before_run
    ]


def test_hook_metadata_includes_docstring():
    HookRegistry.clear()

    @hook(TurnHook.AFTER_RUN)
    async def my_hook(turn):
        """Runs after the turn."""

    assert my_hook.metadata.description == "Runs after the turn."


def test_hook_get_by_type_returns_all_matches_in_order():
    HookRegistry.clear()

    @hook(AgentHook.AFTER_TURN)
    async def first_after(agent, turn):
        pass

    @hook(AgentHook.AFTER_TURN)
    async def second_after(agent, turn):
        pass

    matches = HookRegistry.get_by_type(
        AgentHook.AFTER_TURN, [first_after, second_after]
    )
    assert matches == [first_after, second_after]


def test_hook_memory_hook_type_accepted():
    HookRegistry.clear()

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def before_append(incoming, current):
        pass

    assert before_append.type == ContextQueueHook.BEFORE_APPEND
    assert HookRegistry.get("before_append") is before_append


def test_hook_multi_type_matches_each_type():
    HookRegistry.clear()

    @hook([AgentHook.BEFORE_TURN, AgentHook.AFTER_TURN])
    async def multi_type_hook(*args, **kwargs):
        pass

    assert isinstance(multi_type_hook.type, tuple)
    assert AgentHook.BEFORE_TURN in multi_type_hook.type
    assert AgentHook.AFTER_TURN in multi_type_hook.type
    assert HookRegistry.get_by_type(AgentHook.BEFORE_TURN, [multi_type_hook]) == [
        multi_type_hook
    ]
    assert HookRegistry.get_by_type(AgentHook.AFTER_TURN, [multi_type_hook]) == [
        multi_type_hook
    ]


def test_hook_multi_type_serialization():
    HookRegistry.clear()

    @hook([AgentHook.BEFORE_TURN, AgentHook.AFTER_TURN])
    async def multi_serial_hook(*args, **kwargs):
        pass

    from pygents.utils import serialize_hooks_by_type

    result = serialize_hooks_by_type([multi_serial_hook])
    assert "before_turn" in result
    assert "after_turn" in result
    assert result["before_turn"] == ["multi_serial_hook"]
    assert result["after_turn"] == ["multi_serial_hook"]


def test_hook_multi_type_deduplication_on_deserialization():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook([AgentHook.BEFORE_TURN, AgentHook.AFTER_TURN])
    async def multi_dedup_hook(*args, **kwargs):
        pass

    data = {
        "name": "dedup_agent",
        "description": "Test",
        "tool_names": ["tool_for_hook_test"],
        "queue": [],
        "current_turn": None,
        "hooks": {
            "before_turn": ["multi_dedup_hook"],
            "after_turn": ["multi_dedup_hook"],
        },
    }
    agent = Agent.from_dict(data)
    assert len(agent.hooks) == 1
    assert agent.hooks[0] is multi_dedup_hook


def test_hook_empty_list_raises():
    HookRegistry.clear()
    with pytest.raises(ValueError, match="at least one type"):

        @hook([])
        async def bad_hook(*args, **kwargs):
            pass


# ---------------------------------------------------------------------------
# H2–H3 – hook() decorator: lock
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# H4–H5 – hook() decorator: fixed_kwargs
# ---------------------------------------------------------------------------


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


def test_hook_fixed_kwargs_allowed_with_kwargs():
    HookRegistry.clear()
    received = []

    @hook(TurnHook.BEFORE_RUN, extra="fixed")
    async def accepts_kwargs(turn, **kwargs):
        received.append(kwargs.get("extra"))

    asyncio.run(accepts_kwargs(None))
    assert received == ["fixed"]


# ---------------------------------------------------------------------------
# H6 – hook() decorator: timing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool hooks integration
# ---------------------------------------------------------------------------


def test_tool_with_decorated_hook_registered_in_registry():
    HookRegistry.clear()

    @hook(ToolHook.BEFORE_INVOKE)
    async def registered_tool_hook():
        pass

    @tool()
    async def tool_with_registered_hook() -> None:
        pass

    tool_with_registered_hook.before_invoke(registered_tool_hook)
    assert HookRegistry.get("registered_tool_hook") is registered_tool_hook


# ---------------------------------------------------------------------------
# Turn hooks integration
# ---------------------------------------------------------------------------


def test_turn_hook_append_and_registered():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.hooks.append(turn_hook)
    assert turn_hook in turn.hooks
    assert HookRegistry.get("turn_hook") is turn_hook


def test_turn_hook_registered_under_own_name():
    HookRegistry.clear()

    @hook(TurnHook.AFTER_RUN)
    async def another_turn_hook(turn):
        pass

    turn = Turn("tool_for_hook_test", kwargs={"x": 5})
    turn.hooks.append(another_turn_hook)
    assert HookRegistry.get("another_turn_hook") is another_turn_hook


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


# ---------------------------------------------------------------------------
# Agent hooks integration
# ---------------------------------------------------------------------------


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


def test_agent_hook_registered_under_own_name():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.AFTER_TURN)
    async def another_agent_hook(agent):
        pass

    agent = Agent("hook_agent2", "Test agent", [tool_for_hook_test])
    agent.hooks.append(another_agent_hook)
    assert HookRegistry.get("another_agent_hook") is another_agent_hook


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


def test_agent_multiple_hooks_same_type_all_called():
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.BEFORE_TURN)
    async def first_before_turn_hook(agent):
        events.append("first")

    @hook(AgentHook.BEFORE_TURN)
    async def second_before_turn_hook(agent):
        events.append("second")

    agent = Agent("multi_call_agent", "Test", [tool_for_hook_test])
    agent.hooks = [first_before_turn_hook, second_before_turn_hook]

    async def run():
        await agent.put(Turn("tool_for_hook_test", kwargs={"x": 1}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert events == ["first", "second"]


def test_tool_multiple_hooks_same_type_all_called():
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    async def before_a(*args, **kwargs):
        events.append("a")

    async def before_b(*args, **kwargs):
        events.append("b")

    @tool()
    async def multi_before_tool(x: int) -> int:
        return x

    multi_before_tool.before_invoke(before_a)
    multi_before_tool.before_invoke(before_b)

    agent = Agent("multi_tool_hook_agent", "Test", [multi_before_tool])

    async def run():
        await agent.put(Turn("multi_before_tool", kwargs={"x": 1}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert events == ["a", "b"]


def _collect_async(agen):
    async def run():
        return [x async for x in agen]

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# ContextPool hooks integration
# ---------------------------------------------------------------------------


def test_context_pool_before_add_hook_fires():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.BEFORE_ADD)
    async def cp_before_add(pool, item):
        fired.append(("before_add", item.id, item.id not in pool._items))

    pool = ContextPool(hooks=[cp_before_add])
    asyncio.run(pool.add(ContextItem(id="hello", description="d", content=1)))
    assert fired == [("before_add", "hello", True)]


def test_context_pool_after_add_hook_fires():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.AFTER_ADD)
    async def cp_after_add(pool, item):
        fired.append(("after_add", item.id, item.id in pool._items))

    pool = ContextPool(hooks=[cp_after_add])
    asyncio.run(pool.add(ContextItem(id="world", description="d", content=2)))
    assert fired == [("after_add", "world", True)]


# ---------------------------------------------------------------------------
# Method-decorator hooks (Tool.before_invoke / .after_invoke,
#                          AsyncGenTool.before_invoke / .on_yield / .after_invoke)
# ---------------------------------------------------------------------------


def test_tool_method_before_invoke_registers_with_type():
    HookRegistry.clear()

    @tool()
    async def md_before_tool(x: int) -> int:
        return x

    async def log_before(x: int) -> None:
        pass

    returned = md_before_tool.before_invoke(log_before)
    assert returned is not log_before
    assert returned.fn is log_before
    assert returned.type == ToolHook.BEFORE_INVOKE
    assert (ToolHook.BEFORE_INVOKE, returned) in md_before_tool.hooks
    assert HookRegistry.get(log_before.__name__) is returned


def test_tool_method_after_invoke_registers_with_type():
    HookRegistry.clear()

    @tool()
    async def md_after_tool(x: int) -> int:
        return x

    async def log_after(result: int) -> None:
        pass

    returned = md_after_tool.after_invoke(log_after)
    assert returned is not log_after
    assert returned.fn is log_after
    assert returned.type == ToolHook.AFTER_INVOKE
    assert (ToolHook.AFTER_INVOKE, returned) in md_after_tool.hooks
    assert HookRegistry.get(log_after.__name__) is returned


def test_asyncgen_method_on_yield_registers_with_type():
    HookRegistry.clear()

    @tool()
    async def md_gen_tool(n: int):
        for i in range(n):
            yield i

    async def log_yield(value: int) -> None:
        pass

    returned = md_gen_tool.on_yield(log_yield)
    assert returned is not log_yield
    assert returned.fn is log_yield
    assert returned.type == ToolHook.ON_YIELD
    assert (ToolHook.ON_YIELD, returned) in md_gen_tool.hooks
    assert HookRegistry.get(log_yield.__name__) is returned


def test_asyncgen_method_after_invoke_registers_with_type():
    HookRegistry.clear()

    @tool()
    async def md_gen_after_tool(n: int):
        for i in range(n):
            yield i

    async def log_all(values: list) -> None:
        pass

    returned = md_gen_after_tool.after_invoke(log_all)
    assert returned is not log_all
    assert returned.fn is log_all
    assert returned.type == ToolHook.AFTER_INVOKE
    assert (ToolHook.AFTER_INVOKE, returned) in md_gen_after_tool.hooks
    assert HookRegistry.get(log_all.__name__) is returned


def test_tool_method_before_invoke_fires_end_to_end():
    HookRegistry.clear()
    AgentRegistry.clear()
    received = []

    @tool()
    async def md_before_e2e_tool(x: int) -> int:
        return x * 3

    @md_before_e2e_tool.before_invoke
    async def capture_before(x: int) -> None:
        received.append(x)

    agent = Agent("md_before_e2e_agent", "desc", [md_before_e2e_tool])

    async def run():
        await agent.put(Turn("md_before_e2e_tool", kwargs={"x": 4}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert received == [4]


def test_asyncgen_method_on_yield_fires_end_to_end():
    HookRegistry.clear()
    AgentRegistry.clear()
    received = []

    @tool()
    async def md_on_yield_e2e_tool():
        yield "p"
        yield "q"

    @md_on_yield_e2e_tool.on_yield
    async def capture_yield(value: str) -> None:
        received.append(value)

    agent = Agent("md_on_yield_e2e_agent", "desc", [md_on_yield_e2e_tool])

    async def run():
        await agent.put(Turn("md_on_yield_e2e_tool", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert received == ["p", "q"]


def test_tool_method_decorators_as_decorator_syntax():
    """Verify the @tool.before_invoke decorator syntax works."""
    HookRegistry.clear()
    AgentRegistry.clear()
    log = []

    @tool()
    async def md_decorator_syntax_tool(value: str) -> str:
        return value.upper()

    @md_decorator_syntax_tool.before_invoke
    async def on_before(value: str) -> None:
        log.append(("before", value))

    agent = Agent("md_syntax_agent", "desc", [md_decorator_syntax_tool])

    async def run():
        await agent.put(Turn("md_decorator_syntax_tool", kwargs={"value": "hello"}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert log == [("before", "hello")]


def test_asyncgen_before_invoke_and_on_yield_method_decorators_fire():
    """Verify before_invoke and on_yield fire for AsyncGenTool."""
    HookRegistry.clear()
    AgentRegistry.clear()
    log = []

    @tool()
    async def md_all_hooks_gen_tool(n: int):
        for i in range(n):
            yield i

    @md_all_hooks_gen_tool.before_invoke
    async def gen_before(n: int) -> None:
        log.append(("before", n))

    @md_all_hooks_gen_tool.on_yield
    async def gen_on_yield(value: int) -> None:
        log.append(("yield", value))

    agent = Agent("md_all_hooks_gen_agent", "desc", [md_all_hooks_gen_tool])

    async def run():
        await agent.put(Turn("md_all_hooks_gen_tool", kwargs={"n": 3}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert log[0] == ("before", 3)
    assert log[1] == ("yield", 0)
    assert log[2] == ("yield", 1)
    assert log[3] == ("yield", 2)


# ---------------------------------------------------------------------------
# Method-decorator hooks: registration, context injection, roundtrip
# ---------------------------------------------------------------------------


def test_method_decorator_plain_fn_registered_in_registry():
    """Plain fn attached via method decorator is findable in HookRegistry by name."""
    HookRegistry.clear()

    @tool()
    async def reg_md_tool(x: int) -> int:
        return x

    async def my_reg_hook(x: int) -> None:
        pass

    returned = reg_md_tool.before_invoke(my_reg_hook)
    assert HookRegistry.get(my_reg_hook.__name__) is returned


