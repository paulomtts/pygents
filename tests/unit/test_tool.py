"""
Tests for pygents.tool, driven by the following decision table.

Decision table for pygents/tool.py
-----------------------------------
Decorator entry:
  E1  func is not None (@tool no parens) -> return decorator(func)
  E2  func is None (@tool()) -> return decorator

Function type:
  F1  Coroutine function -> coroutine path (single result)
  F2  Async generator function -> async-gen path (yield loop, ON_YIELD, AFTER_INVOKE with list of all yielded values)
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

Invocation (coroutine): start_time, BEFORE_INVOKE, await fn, end_time in finally.
Invocation (async gen): start_time, BEFORE_INVOKE, async for + ON_YIELD, end_time in finally.
  R1  ToolRegistry.register(wrapper) after build
  M1  ToolMetadata.dict(): start_time/end_time -> None or isoformat
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import pytest

from pygents.context import ContextPool, ContextQueue
from pygents.context import _current_context_pool, _current_context_queue
from pygents.hooks import ToolHook
from pygents.registry import ToolRegistry
from pygents.tool import ToolMetadata, _inject_context_deps, tool


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


def test_before_invoke_hook_fires_on_coroutine_tool():
    events = []

    async def before(x):
        events.append(("before", x))

    before.type = ToolHook.BEFORE_INVOKE  # type: ignore[attr-defined]

    @tool(hooks=[before])
    async def add_one(x: int) -> int:
        return x + 1

    asyncio.run(add_one(7))
    assert events == [("before", 7)]


def test_on_yield_hook_fires_on_async_gen_tool():
    yields_seen = []

    async def on_yield(value):
        yields_seen.append(value)

    on_yield.type = ToolHook.ON_YIELD        # type: ignore[attr-defined]

    @tool(hooks=[on_yield])
    async def gen_two():
        yield 10
        yield 20

    _collect_async(gen_two())
    assert yields_seen == [10, 20]


# ---------------------------------------------------------------------------
# Context injection tests
# ---------------------------------------------------------------------------


def test_tool_injects_context_queue_when_var_is_set():
    received = []

    @tool()
    async def needs_queue(x: int, memory: ContextQueue) -> int:
        received.append(memory)
        return x

    cq = ContextQueue(limit=5)

    async def run():
        token = _current_context_queue.set(cq)
        try:
            return await needs_queue(x=3)
        finally:
            _current_context_queue.reset(token)

    result = asyncio.run(run())
    assert result == 3
    assert len(received) == 1
    assert received[0] is cq


def test_tool_injects_context_pool_when_var_is_set():
    received = []

    @tool()
    async def needs_pool(x: int, pool: ContextPool) -> int:
        received.append(pool)
        return x

    cp = ContextPool()

    async def run():
        token = _current_context_pool.set(cp)
        try:
            return await needs_pool(x=7)
        finally:
            _current_context_pool.reset(token)

    result = asyncio.run(run())
    assert result == 7
    assert len(received) == 1
    assert received[0] is cp


def test_tool_injects_optional_context_queue_when_var_is_set():
    received = []

    @tool()
    async def optional_queue(x: int, memory: ContextQueue | None = None) -> int:
        received.append(memory)
        return x

    cq = ContextQueue(limit=5)

    async def run():
        token = _current_context_queue.set(cq)
        try:
            return await optional_queue(x=1)
        finally:
            _current_context_queue.reset(token)

    result = asyncio.run(run())
    assert result == 1
    assert received[0] is cq


def test_tool_optional_context_queue_falls_back_to_none_when_var_unset():
    received = []

    @tool()
    async def optional_queue_unset(x: int, memory: ContextQueue | None = None) -> int:
        received.append(memory)
        return x

    result = asyncio.run(optional_queue_unset(x=2))
    assert result == 2
    assert received[0] is None


def test_explicit_kwarg_overrides_context_injection():
    received = []

    @tool()
    async def overridable_queue(x: int, memory: ContextQueue) -> int:
        received.append(memory)
        return x

    cq_injected = ContextQueue(limit=5)
    cq_explicit = ContextQueue(limit=3)

    async def run():
        token = _current_context_queue.set(cq_injected)
        try:
            return await overridable_queue(x=5, memory=cq_explicit)
        finally:
            _current_context_queue.reset(token)

    result = asyncio.run(run())
    assert result == 5
    assert received[0] is cq_explicit  # explicit wins over injection


def test_tool_without_context_typed_params_unaffected():
    @tool()
    async def plain_tool(a: int, b: int) -> int:
        return a + b

    cq = ContextQueue(limit=5)

    async def run():
        token = _current_context_queue.set(cq)
        try:
            return await plain_tool(a=2, b=3)
        finally:
            _current_context_queue.reset(token)

    result = asyncio.run(run())
    assert result == 5


def test_async_gen_tool_injects_context_queue():
    received = []

    @tool()
    async def gen_needs_queue(memory: ContextQueue):
        received.append(memory)
        yield len(memory)

    cq = ContextQueue(limit=5)

    async def run():
        token = _current_context_queue.set(cq)
        try:
            return _collect_async(gen_needs_queue())
        finally:
            _current_context_queue.reset(token)

    # Must set ContextVar before collection starts
    async def run_inner():
        token = _current_context_queue.set(cq)
        try:
            out = []
            async for v in gen_needs_queue():
                out.append(v)
            return out
        finally:
            _current_context_queue.reset(token)

    results = asyncio.run(run_inner())
    assert len(received) == 1
    assert received[0] is cq
    assert results == [0]


def test_async_gen_tool_injects_context_pool():
    received = []

    @tool()
    async def gen_needs_pool(pool: ContextPool):
        received.append(pool)
        yield len(pool)

    cp = ContextPool()

    async def run_inner():
        token = _current_context_pool.set(cp)
        try:
            out = []
            async for v in gen_needs_pool():
                out.append(v)
            return out
        finally:
            _current_context_pool.reset(token)

    results = asyncio.run(run_inner())
    assert len(received) == 1
    assert received[0] is cp
    assert results == [0]


def test_inject_context_deps_handles_unresolvable_hints():
    """_inject_context_deps returns merged unchanged when get_type_hints fails."""

    def bad_hints_fn(x: "NonExistentType") -> None:  # type: ignore[name-defined]  # noqa: F821
        pass

    merged = {"x": 1}
    result = _inject_context_deps(bad_hints_fn, merged)
    assert result == {"x": 1}
