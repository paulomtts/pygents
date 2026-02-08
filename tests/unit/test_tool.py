import asyncio
import logging
from typing import Any

import pytest

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


def test_tool_fixed_kwargs_merged_into_invocation():
    @tool(permission="admin")
    async def fixed_kwargs_tool(value: int, permission: str) -> tuple[int, str]:
        return (value, permission)

    result = asyncio.run(fixed_kwargs_tool(10))
    assert result == (10, "admin")


def test_tool_fixed_kwargs_call_time_override():
    @tool(permission="admin")
    async def override_tool(permission: str) -> str:
        return permission

    result = asyncio.run(override_tool(permission="user"))
    assert result == "user"


def test_tool_fixed_kwargs_multiple_keys():
    @tool(a=1, b=2)
    async def multi_fixed(a: int, b: int, c: int) -> int:
        return a + b + c

    result = asyncio.run(multi_fixed(c=3))
    assert result == 6


def test_tool_fixed_kwargs_lambda_evaluated_at_invoke_time():
    counter = [0]

    @tool(n=lambda: counter[0])
    async def lambda_fixed_tool(n: int) -> int:
        return n

    counter[0] = 5
    assert asyncio.run(lambda_fixed_tool()) == 5
    counter[0] = 11
    assert asyncio.run(lambda_fixed_tool()) == 11


def test_tool_fixed_kwargs_async_gen_tool():
    @tool(prefix="fixed-")
    async def yielding_fixed_tool(prefix: str, x: int):
        yield f"{prefix}{x}"
        yield f"{prefix}{x + 1}"

    results = _collect_async(yielding_fixed_tool(x=1))
    assert results == ["fixed-1", "fixed-2"]


def test_tool_fixed_kwarg_not_in_signature_raises():
    with pytest.raises(TypeError, match="fixed kwargs .* are not in function signature"):

        @tool(unknown_param=1)
        async def no_such_param(x: int) -> int:
            return x


def test_tool_fixed_kwargs_allowed_when_function_has_kwargs():
    @tool(extra="fixed")
    async def accepts_kwargs(x: int, **kwargs: Any) -> dict:
        return {"x": x, **kwargs}

    result = asyncio.run(accepts_kwargs(1))
    assert result == {"x": 1, "extra": "fixed"}


def test_tool_fixed_kwarg_overridden_logs_warning(caplog):
    @tool(permission="admin")
    async def override_tool_logs(permission: str) -> str:
        return permission

    with caplog.at_level(logging.WARNING, logger="pygents"):
        asyncio.run(override_tool_logs(permission="user"))
    assert "Fixed kwarg 'permission' is overridden" in caplog.text
    assert "override_tool_logs" in caplog.text


def _collect_async(agen):
    async def run():
        out = []
        async for x in agen:
            out.append(x)
        return out

    return asyncio.run(run())
