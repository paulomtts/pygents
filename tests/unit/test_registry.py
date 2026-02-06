import pytest

from app.enums import ToolType
from app.registry import ToolRegistry
from app.tool import tool


def test_get_returns_registered_tool():
    @tool(type=ToolType.ACTION)
    def add(a: int, b: int) -> int:
        return a + b

    retrieved = ToolRegistry.get("add")
    assert retrieved is add


def test_get_missing_raises_key_error():
    with pytest.raises(KeyError, match=r"'nonexistent' not found"):
        ToolRegistry.get("nonexistent")


def test_register_duplicate_name_raises_value_error():
    @tool()
    def duplicate_name() -> None:
        pass

    with pytest.raises(ValueError, match=r"'duplicate_name' already registered"):
        ToolRegistry.register(duplicate_name)
