import asyncio

from pygents.registry import ToolRegistry
from pygents.tool import ToolMetadata, tool


def test_decorated_function_preserves_behavior():
    @tool()
    async def double(x: int) -> int:
        return x * 2

    assert asyncio.run(double(3)) == 6


def test_metadata_name_and_description():
    @tool()
    async def noop() -> None:
        """A noop tool."""
        pass

    assert noop.metadata.name == "noop"
    assert noop.metadata.description == "A noop tool."


def test_metadata_dict_returns_asdict():
    @tool()
    async def read() -> None:
        pass

    result = read.metadata.dict()
    assert result == {
        "name": "read",
        "description": None,
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

    assert bare.metadata.name == "bare"
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
    )
    assert metadata.name == "foo"
    assert metadata.description == "A tool."
