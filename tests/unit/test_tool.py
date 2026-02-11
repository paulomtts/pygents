"""
Tests for pygents.tool, driven by the following decision table.

Decision table for pygents/tool.py
-----------------------------------
Decorator entry:
  E1  func is not None (@tool no parens) -> return decorator(func)
  E2  func is None (@tool()) -> return decorator

Function type:
  F1  Coroutine function -> coroutine path (single result)
  F2  Async generator function -> async-gen path (yield loop, ON_YIELD, AFTER_INVOKE with last value)
  F3  Sync function -> TypeError "Tool must be async"

Fixed kwargs:
  K1  Key not in signature and no **kwargs -> TypeError
  K2  **kwargs in signature -> fixed keys allowed
  K3  Call-time overrides fixed -> merge_kwargs logs WARNING

Lock:
  L1  lock=False -> wrapper.lock is None
  L2  lock=True -> wrapper.lock is asyncio.Lock()

Hooks:
  H1  hooks=None -> wrapper.hooks = []
  H2  hooks=[...] -> wrapper.hooks = list(hooks)

Invocation (coroutine): start_time, BEFORE_INVOKE, await fn, AFTER_INVOKE, end_time in finally.
Invocation (async gen): start_time, BEFORE_INVOKE, async for + ON_YIELD, AFTER_INVOKE(last), end_time in finally.
  R1  ToolRegistry.register(wrapper) after build
  M1  ToolMetadata.dict(): start_time/end_time -> None or isoformat
"""

import asyncio
import logging
from datetime import datetime
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
        "start_time": None,
        "end_time": None,
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


def test_tool_sync_function_raises_type_error():
    with pytest.raises(TypeError, match="Tool must be async"):

        @tool()
        def sync_tool() -> int:
            return 1


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


def test_decorated_tool_has_hooks_list_empty_when_none():
    @tool()
    async def no_hooks() -> None:
        pass

    assert hasattr(no_hooks, "hooks")
    assert no_hooks.hooks == []


def test_decorated_tool_hooks_list_stored_when_provided():
    hook = object()

    @tool(hooks=[hook])
    async def with_hooks() -> None:
        pass

    assert with_hooks.hooks == [hook]


def test_tool_metadata_fields():
    metadata = ToolMetadata(
        name="foo",
        description="A tool.",
    )
    assert metadata.name == "foo"
    assert metadata.description == "A tool."
    assert metadata.start_time is None
    assert metadata.end_time is None


def test_tool_fixed_kwargs_merged_into_invocation():
    @tool(permission="admin")
    async def fixed_kwargs_tool(value: int, permission: str) -> tuple[int, str]:
        return (value, permission)

    result = asyncio.run(fixed_kwargs_tool(10))
    assert result == (10, "admin")


def test_tool_fixed_kwargs_call_time_override_and_logs_warning(caplog):
    @tool(permission="admin")
    async def override_tool(permission: str) -> str:
        return permission

    with caplog.at_level(logging.WARNING, logger="pygents"):
        result = asyncio.run(override_tool(permission="user"))
    assert result == "user"
    assert "Fixed kwarg 'permission' is overridden" in caplog.text
    assert "override_tool" in caplog.text


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


def test_tool_metadata_start_time_and_end_time_set_on_run():
    @tool()
    async def timed_tool() -> str:
        return "ok"

    assert timed_tool.metadata.start_time is None
    assert timed_tool.metadata.end_time is None
    asyncio.run(timed_tool())
    assert timed_tool.metadata.start_time is not None
    assert timed_tool.metadata.end_time is not None
    assert timed_tool.metadata.start_time <= timed_tool.metadata.end_time


def test_tool_metadata_dict_after_run_returns_isoformat_times():
    @tool()
    async def timed_tool_iso() -> str:
        return "ok"

    asyncio.run(timed_tool_iso())
    result = timed_tool_iso.metadata.dict()
    assert result["start_time"] is not None
    assert result["end_time"] is not None
    datetime.fromisoformat(result["start_time"])
    datetime.fromisoformat(result["end_time"])


def test_tool_end_time_set_when_invocation_raises():
    @tool()
    async def failing_tool() -> None:
        raise ValueError("tool failed")

    assert failing_tool.metadata.end_time is None
    with pytest.raises(ValueError, match="tool failed"):
        asyncio.run(failing_tool())
    assert failing_tool.metadata.end_time is not None


def test_tool_lock_serializes_concurrent_calls():
    order = []

    @tool(lock=True)
    async def serialized(x: int) -> int:
        order.append(("start", x))
        await asyncio.sleep(0.02)
        order.append(("end", x))
        return x

    async def run_concurrent():
        await asyncio.gather(serialized(1), serialized(2))

    asyncio.run(run_concurrent())
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


def _collect_async(agen):
    async def run():
        out = []
        async for x in agen:
            out.append(x)
        return out

    return asyncio.run(run())
