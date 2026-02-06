import pytest

from app.registry import ToolRegistry
from app.tool import tool, ToolType


def test_get_returns_registered_tool():
    @tool(type=ToolType.ACTION)
    async def add_test(a: int, b: int) -> int:
        return a + b

    retrieved = ToolRegistry.get("add_test")
    assert retrieved is add_test


def test_get_missing_raises_key_error():
    with pytest.raises(KeyError, match=r"'nonexistent' not found"):
        ToolRegistry.get("nonexistent")


def test_register_duplicate_name_raises_value_error():
    @tool()
    async def duplicate_name() -> None:
        pass

    with pytest.raises(ValueError, match=r"'duplicate_name' already registered"):
        ToolRegistry.register(duplicate_name)
