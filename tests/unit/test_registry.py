import pytest

from pygents.agent import Agent
from pygents.errors import UnregisteredAgentError, UnregisteredToolError
from pygents.registry import AgentRegistry, ToolRegistry
from pygents.tool import tool, ToolType


def test_get_returns_registered_tool():
    @tool(type=ToolType.ACTION)
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


@tool(type=ToolType.ACTION)
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
