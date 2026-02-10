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


def test_get_missing_raises_key_error():
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


def test_hook_registry_register_and_get():
    HookRegistry.clear()

    async def my_hook():
        pass

    HookRegistry.register(my_hook)
    retrieved = HookRegistry.get("my_hook")
    assert retrieved is my_hook


def test_hook_registry_register_with_custom_name():
    HookRegistry.clear()

    async def some_hook():
        pass

    HookRegistry.register(some_hook, name="custom_name")
    retrieved = HookRegistry.get("custom_name")
    assert retrieved is some_hook


def test_hook_registry_get_missing_raises_unregistered_hook_error():
    HookRegistry.clear()
    with pytest.raises(UnregisteredHookError, match=r"'nonexistent' not found"):
        HookRegistry.get("nonexistent")


def test_hook_registry_register_duplicate_raises_value_error():
    HookRegistry.clear()

    async def duplicate_hook():
        pass

    HookRegistry.register(duplicate_hook)
    with pytest.raises(ValueError, match=r"'duplicate_hook' already registered"):
        HookRegistry.register(duplicate_hook)


def test_hook_registry_clear():
    HookRegistry.clear()

    async def clearable_hook():
        pass

    HookRegistry.register(clearable_hook)
    assert HookRegistry.get("clearable_hook") is clearable_hook

    HookRegistry.clear()
    with pytest.raises(UnregisteredHookError):
        HookRegistry.get("clearable_hook")
