"""
Tests for pygents.registry, driven by the following decision table.

Decision table for pygents/registry.py
--------------------------------------
ToolRegistry:
  TR1  register(tool): name already in _registry -> ValueError "already registered"
  TR2  register(tool): else -> _registry[tool.__name__] = tool
  TR3  get(name): not in _registry -> UnregisteredToolError
  TR4  get(name): in _registry -> return tool
  TR5  all() -> list(_registry.values())

AgentRegistry:
  AR1  clear() -> _registry = {}
  AR2  register(agent): agent.name already in _registry -> ValueError
  AR3  register(agent): else -> _registry[agent.name] = agent
  AR4  get(name): not in _registry -> UnregisteredAgentError
  AR5  get(name): in _registry -> return agent

HookRegistry:
  HR1  clear() -> _registry = {}
  HR2  register(hook, name=None, hook_type=None): hook_name = name or __name__ or "hook"
  HR3  register: different hook already under hook_name -> ValueError "already registered"
  HR4  register: same hook instance again -> no error (overwrite)
  HR5  register(..., hook_type=X) -> hook.hook_type = X
  HR6  get(name): not in _registry -> UnregisteredHookError
  HR7  get(name): in _registry -> return hook
  HR8  get_by_type(hook_type, hooks) -> first h in hooks with hook_type match, else None
"""

import pytest

from pygents.agent import Agent
from pygents.errors import (
    UnregisteredAgentError,
    UnregisteredHookError,
    UnregisteredToolError,
)
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import tool


def test_get_returns_registered_tool():
    @tool()
    async def add_test(a: int, b: int) -> int:
        return a + b

    retrieved = ToolRegistry.get("add_test")
    assert retrieved is add_test


def test_get_missing_raises_unregistered_tool_error():
    with pytest.raises(UnregisteredToolError, match=r"'nonexistent' not found"):
        ToolRegistry.get("nonexistent")


def test_register_duplicate_name_raises_value_error():
    @tool()
    async def duplicate_name() -> None:
        pass

    with pytest.raises(ValueError, match=r"'duplicate_name' already registered"):
        ToolRegistry.register(duplicate_name)


def test_all_returns_all_registered_tools():
    @tool()
    async def tool_one(x: int) -> int:
        return x

    @tool()
    async def tool_two(y: str) -> str:
        return y

    @tool()
    async def tool_three(z: float) -> float:
        return z

    all_tools = ToolRegistry.all()
    assert tool_one in all_tools
    assert tool_two in all_tools
    assert tool_three in all_tools
    assert len(all_tools) >= 3


@tool()
async def _registry_test_tool(x: int) -> int:
    return x


def test_agent_registry_get_returns_registered_agent():
    AgentRegistry.clear()
    agent = Agent("registry_test_agent", "For registry tests", [_registry_test_tool])
    retrieved = AgentRegistry.get("registry_test_agent")
    assert retrieved is agent


def test_agent_registry_get_missing_raises_unregistered_agent_error():
    with pytest.raises(UnregisteredAgentError, match=r"'nonexistent_agent' not found"):
        AgentRegistry.get("nonexistent_agent")


def test_agent_registry_register_duplicate_name_raises_value_error():
    AgentRegistry.clear()
    Agent("duplicate_agent", "First", [_registry_test_tool])
    with pytest.raises(ValueError, match=r"'duplicate_agent' already registered"):
        Agent("duplicate_agent", "Second", [_registry_test_tool])


def test_agent_registry_clear_empties_registry():
    AgentRegistry.clear()
    agent = Agent("clearable_agent", "Desc", [_registry_test_tool])
    assert AgentRegistry.get("clearable_agent") is agent
    AgentRegistry.clear()
    with pytest.raises(UnregisteredAgentError, match="clearable_agent"):
        AgentRegistry.get("clearable_agent")


def test_hook_registry_register_and_get():
    HookRegistry.clear()

    async def my_hook():
        pass

    HookRegistry.register(my_hook)
    retrieved = HookRegistry.get("my_hook")
    assert retrieved is my_hook



def test_hook_registry_get_missing_raises_unregistered_hook_error():
    HookRegistry.clear()
    with pytest.raises(UnregisteredHookError, match=r"'nonexistent' not found"):
        HookRegistry.get("nonexistent")


def test_hook_registry_register_duplicate_raises_value_error():
    HookRegistry.clear()

    async def duplicate_hook():
        pass

    async def other_hook():
        pass

    other_hook.__name__ = "duplicate_hook"

    HookRegistry.register(duplicate_hook)
    with pytest.raises(ValueError, match=r"'duplicate_hook' already registered"):
        HookRegistry.register(other_hook)


def test_hook_registry_reregister_same_hook_does_not_raise():
    HookRegistry.clear()

    async def same_hook():
        pass

    HookRegistry.register(same_hook)
    HookRegistry.register(same_hook)
    assert HookRegistry.get("same_hook") is same_hook


def test_hook_registry_register_with_hook_type_stores_and_findable():
    from pygents.hooks import TurnHook, hook

    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def before_run(turn):
        pass

    assert getattr(before_run, "type", None) == TurnHook.BEFORE_RUN
    found = HookRegistry.get_by_type(TurnHook.BEFORE_RUN, [before_run])
    assert found == [before_run]


def test_hook_registry_clear():
    HookRegistry.clear()

    async def clearable_hook():
        pass

    HookRegistry.register(clearable_hook)
    assert HookRegistry.get("clearable_hook") is clearable_hook

    HookRegistry.clear()
    with pytest.raises(UnregisteredHookError):
        HookRegistry.get("clearable_hook")


def test_hook_registry_get_by_type():
    from pygents.hooks import AgentHook, TurnHook

    HookRegistry.clear()

    async def turn_before(turn):
        pass

    async def agent_after(agent, turn):
        pass

    turn_before.type = TurnHook.BEFORE_RUN  # type: ignore[attr-defined]
    agent_after.type = AgentHook.AFTER_TURN  # type: ignore[attr-defined]

    before_run = HookRegistry.get_by_type(TurnHook.BEFORE_RUN, [turn_before])
    assert before_run == [turn_before]

    after_turn = HookRegistry.get_by_type(AgentHook.AFTER_TURN, [agent_after])
    assert after_turn == [agent_after]

    empty = HookRegistry.get_by_type(TurnHook.AFTER_RUN, [])
    assert empty == []


def test_hook_registry_get_by_type_with_frozenset():
    from pygents.hooks import AgentHook

    HookRegistry.clear()

    async def my_hook(agent):
        pass

    object.__setattr__(my_hook, "type", frozenset({AgentHook.BEFORE_TURN}))

    result = HookRegistry.get_by_type(AgentHook.BEFORE_TURN, [my_hook])
    assert result == [my_hook]
    result_miss = HookRegistry.get_by_type(AgentHook.AFTER_TURN, [my_hook])
    assert result_miss == []


def test_hook_registry_get_by_type_returns_all_matches():
    from pygents.hooks import TurnHook

    HookRegistry.clear()

    async def first_hook(turn):
        pass

    async def second_hook(turn):
        pass

    first_hook.type = TurnHook.BEFORE_RUN  # type: ignore[attr-defined]
    second_hook.type = TurnHook.BEFORE_RUN  # type: ignore[attr-defined]

    matches = HookRegistry.get_by_type(
        TurnHook.BEFORE_RUN, [first_hook, second_hook]
    )
    assert matches == [first_hook, second_hook]


def test_get_by_type_returns_empty_for_typeless_hook():
    """Covers the `ht is None` guard in registry.py.

    A plain callable without a `.type` attribute must be treated as
    non-matching, returning an empty list.
    """
    from pygents.hooks import TurnHook

    async def typeless_hook(turn):
        pass

    # No .type attribute set — getattr returns None → matches() returns False
    result = HookRegistry.get_by_type(TurnHook.BEFORE_RUN, [typeless_hook])
    assert result == []


# ---------------------------------------------------------------------------
# HookRegistry.wrap
# ---------------------------------------------------------------------------


def test_wrap_plain_fn_creates_and_registers_hook():
    from pygents.hooks import TurnHook

    HookRegistry.clear()

    async def my_plain_hook(turn):
        pass

    wrapped = HookRegistry.wrap(my_plain_hook, TurnHook.BEFORE_RUN)
    assert wrapped is not my_plain_hook
    assert wrapped.fn is my_plain_hook
    assert wrapped.type == TurnHook.BEFORE_RUN
    assert HookRegistry.get("my_plain_hook") is wrapped


def test_wrap_already_wrapped_hook_returns_same_object():
    from pygents.hooks import TurnHook, hook

    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def existing_hook(turn):
        pass

    result = HookRegistry.wrap(existing_hook, TurnHook.BEFORE_RUN)
    assert result is existing_hook


def test_wrap_previously_registered_fn_reuses_wrapper():
    from pygents.hooks import TurnHook

    HookRegistry.clear()

    async def reusable_hook(turn):
        pass

    first = HookRegistry.wrap(reusable_hook, TurnHook.BEFORE_RUN)
    second = HookRegistry.wrap(reusable_hook, TurnHook.BEFORE_RUN)
    assert first is second
