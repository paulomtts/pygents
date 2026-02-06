import asyncio

import pytest

from pygents.registry import ToolRegistry
from pygents.tool import ToolMetadata, tool, ToolType


def test_decorated_function_preserves_behavior():
    @tool()
    async def double(x: int) -> int:
        return x * 2

    assert asyncio.run(double(3)) == 6


def test_metadata_default_type_and_approval():
    @tool()
    async def noop() -> None:
        pass

    assert noop.metadata.name == "noop"
    assert noop.metadata.description is None
    assert noop.metadata.type == ToolType.ACTION
    assert noop.metadata.approval is False


def test_metadata_custom_type_and_approval():
    @tool(type=ToolType.REASONING, approval=True)
    async def think() -> None:
        pass

    assert think.metadata.name == "think"
    assert think.metadata.description is None
    assert think.metadata.type == ToolType.REASONING
    assert think.metadata.approval is True


def test_metadata_dict_returns_asdict():
    @tool(type=ToolType.MEMORY_READ)
    async def read() -> None:
        pass

    result = read.metadata.dict()
    assert result == {
        "name": "read",
        "description": None,
        "type": ToolType.MEMORY_READ,
        "approval": False,
    }


def test_decorated_function_registered():
    @tool()
    async def unique_test_fn() -> str:
        return "ok"

    registered = ToolRegistry.get("unique_test_fn")
    assert registered is unique_test_fn
    assert asyncio.run(registered()) == "ok"


def test_decorator_without_parentheses():
    @tool
    async def bare() -> int:
        return 1

    assert bare.metadata.type == ToolType.ACTION
    assert asyncio.run(bare()) == 1


def test_decorated_tool_default_no_lock():
    @tool()
    async def no_lock() -> None:
        pass

    assert hasattr(no_lock, "lock")
    assert no_lock.lock is None


def test_decorated_tool_lock_true_has_lock():
    @tool(lock=True)
    async def with_lock() -> None:
        pass

    assert hasattr(with_lock, "lock")
    assert isinstance(with_lock.lock, asyncio.Lock)


def test_decorated_tool_has_hooks_dict():
    @tool()
    async def with_hooks() -> None:
        pass

    assert hasattr(with_hooks, "hooks")
    assert with_hooks.hooks == {}


def test_tool_metadata_namedtuple_fields():
    metadata = ToolMetadata(
        name="foo",
        description="A tool.",
        type=ToolType.ACTION,
        approval=True,
    )
    assert metadata.name == "foo"
    assert metadata.description == "A tool."
    assert metadata.type == ToolType.ACTION
    assert metadata.approval is True


def test_completion_check_requires_return_annotation_bool():
    async def no_return_annotation():
        return True

    with pytest.raises(TypeError, match="return type bool"):
        tool(type=ToolType.COMPLETION_CHECK)(no_return_annotation)


def test_completion_check_rejects_async_generator():
    async def yielding():
        yield True

    with pytest.raises(TypeError, match="coroutine, not an async generator"):
        tool(type=ToolType.COMPLETION_CHECK)(yielding)
