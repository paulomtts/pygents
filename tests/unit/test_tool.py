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
  H1  wrapper.hooks starts empty; method decorators append to it

Invocation (coroutine): start_time, BEFORE_INVOKE, await fn, end_time in finally.
Invocation (async gen): start_time, BEFORE_INVOKE, async for + ON_YIELD, end_time in finally.
  R1  ToolRegistry.register(wrapper) after build
  M1  ToolMetadata.dict(): start_time/end_time -> None or isoformat

Subtools / doc_tree:
  S1  @parent.subtool() registers child in ToolRegistry and parent.add_subtool(child)
  S2  doc_tree() returns {"name", "description", "subtools": [...]} recursively, no timing
  S3  subtool(lock=True) etc. pass through to child
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Coroutine, cast

import pytest

from pygents.context import (
    ContextPool,
    ContextQueue,
    _current_context_pool,
    _current_context_queue,
)
from pygents.errors import UnregisteredToolError
from pygents.hooks import ToolHook
from pygents.registry import ToolRegistry
from pygents.tool import ToolMetadata, inject_context_deps, tool


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

        @tool()  # type: ignore[arg-type]
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

    result = asyncio.run(fixed_kwargs_tool(10))  # type: ignore[call-arg]  # permission autoinjected
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

    result = asyncio.run(multi_fixed(c=3))  # type: ignore[call-arg]  # c autoinjected
    assert result == 6


def test_tool_fixed_kwargs_lambda_evaluated_at_invoke_time():
    counter = [0]

    @tool(n=lambda: counter[0])
    async def lambda_fixed_tool(n: int) -> int:
        return n

    counter[0] = 5
    assert asyncio.run(lambda_fixed_tool()) == 5  # type: ignore[call-arg]  # n autoinjected
    counter[0] = 11
    assert asyncio.run(lambda_fixed_tool()) == 11  # type: ignore[call-arg]  # n autoinjected


def test_tool_fixed_kwargs_async_gen_tool(collect_async):
    @tool(prefix="fixed-")
    async def yielding_fixed_tool(prefix: str, x: int):
        yield f"{prefix}{x}"
        yield f"{prefix}{x + 1}"

    results = collect_async(yielding_fixed_tool(x=1))  # type: ignore[call-arg]  # prefix autoinjected
    assert results == ["fixed-1", "fixed-2"]


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


def test_before_invoke_hook_fires_on_coroutine_tool():
    events = []

    @tool()
    async def add_one(x: int) -> int:
        return x + 1

    @add_one.before_invoke
    async def before(x):
        events.append(("before", x))

    asyncio.run(add_one(7))
    assert events == [("before", 7)]


def test_stacked_decorator_shared_hook_fires_for_both_tools():
    """Sharing a hook via stacked decorators must fire for both tools."""
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    events = []

    @tool()
    async def tool_a(x: int) -> int:
        return x

    @tool()
    async def tool_b(x: int) -> int:
        return x

    @tool_a.after_invoke
    @tool_b.before_invoke
    async def shared_hook(x):
        events.append(x)

    async def run():
        await tool_b._run_hooks(ToolHook.BEFORE_INVOKE, 1)
        await tool_a._run_hooks(ToolHook.AFTER_INVOKE, 2)

    asyncio.run(run())
    assert events == [1, 2]


def test_on_yield_hook_fires_on_async_gen_tool(collect_async):
    yields_seen = []

    @tool()
    async def gen_two():
        yield 10
        yield 20

    @gen_two.on_yield
    async def on_yield(value):
        yields_seen.append(value)

    collect_async(gen_two())
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
            return await needs_queue(x=3)  # type: ignore[call-arg]  # memory autoinjected
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
            return await needs_pool(x=7)  # type: ignore[call-arg]  # pool autoinjected
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

    async def run_inner():
        token = _current_context_queue.set(cq)
        try:
            out = []
            async for v in gen_needs_queue():  # type: ignore[call-arg]  # memory autoinjected
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
            async for v in gen_needs_pool():  # type: ignore[call-arg]  # pool autoinjected
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
    result = inject_context_deps(bad_hints_fn, merged)
    assert result == {"x": 1}


# ---------------------------------------------------------------------------
# AFTER_INVOKE dispatch (verification tests)
# ---------------------------------------------------------------------------


def test_tool_after_invoke_receives_result():
    """after_invoke fires with the tool's return value."""
    received = []

    @tool()
    async def verify_after_invoke(x: int) -> int:
        return x * 3

    @verify_after_invoke.after_invoke
    async def capture(result: int) -> None:
        received.append(result)

    asyncio.run(verify_after_invoke(x=4))
    assert received == [12]


def test_asyncgen_after_invoke_receives_aggregated_list(collect_async):
    """after_invoke on AsyncGenTool fires with a list of all yielded values."""
    received = []

    @tool()
    async def verify_gen_after_invoke():
        yield "a"
        yield "b"
        yield "c"

    @verify_gen_after_invoke.after_invoke
    async def capture_gen(values: list) -> None:
        received.append(values)

    collect_async(verify_gen_after_invoke())
    assert received == [["a", "b", "c"]]


def test_after_invoke_does_not_fire_when_tool_raises():
    """AFTER_INVOKE must NOT dispatch if the coroutine tool raises."""
    fired = []

    @tool()
    async def raising_ai_tool() -> None:
        raise RuntimeError("boom")

    @raising_ai_tool.after_invoke
    async def should_not_run(result) -> None:
        fired.append(result)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(raising_ai_tool())
    assert fired == []


def test_after_invoke_does_not_fire_when_async_gen_raises(collect_async):
    """AFTER_INVOKE must NOT dispatch if the async gen tool raises mid-iteration."""
    fired = []

    @tool()
    async def raising_gen_tool():
        yield 1
        raise RuntimeError("gen boom")

    @raising_gen_tool.after_invoke
    async def should_not_run_gen(values: list) -> None:
        fired.append(values)

    with pytest.raises(RuntimeError, match="gen boom"):
        collect_async(raising_gen_tool())
    assert fired == []


# ---------------------------------------------------------------------------
# ON_ERROR tests
# ---------------------------------------------------------------------------


def test_on_error_fires_when_tool_raises():
    """ON_ERROR hook receives the exception when a coroutine tool raises."""
    received = []

    @tool()
    async def erroring_tool() -> None:
        raise ValueError("boom")

    @erroring_tool.on_error
    async def capture_err(exc) -> None:
        received.append(exc)

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(erroring_tool())
    assert len(received) == 1
    assert isinstance(received[0], ValueError)


def test_on_error_does_not_fire_when_tool_succeeds():
    """ON_ERROR must NOT fire when the tool returns normally."""
    fired = []

    @tool()
    async def happy_tool() -> int:
        return 42

    @happy_tool.on_error
    async def on_error_not_called_on_success(exc) -> None:
        fired.append(exc)

    asyncio.run(happy_tool())
    assert fired == []


def test_after_invoke_does_not_fire_when_tool_raises_with_on_error():
    """AFTER_INVOKE must NOT fire when the tool raises, even with ON_ERROR registered."""
    after_fired = []
    error_fired = []

    @tool()
    async def raising_both_hooks() -> None:
        raise RuntimeError("expected")

    @raising_both_hooks.after_invoke
    async def after_hook(result) -> None:
        after_fired.append(result)

    @raising_both_hooks.on_error
    async def error_hook(exc) -> None:
        error_fired.append(exc)

    with pytest.raises(RuntimeError, match="expected"):
        asyncio.run(raising_both_hooks())
    assert after_fired == []
    assert len(error_fired) == 1


def test_on_error_fires_when_async_gen_raises_mid_iteration(collect_async):
    """ON_ERROR fires when async gen raises mid-iteration; AFTER_INVOKE does not."""
    error_received = []
    after_fired = []

    @tool()
    async def partial_gen():
        yield 1
        raise RuntimeError("mid-gen error")

    @partial_gen.on_error
    async def capture_gen_err(exc) -> None:
        error_received.append(exc)

    @partial_gen.after_invoke
    async def after_gen(values: list) -> None:
        after_fired.append(values)

    with pytest.raises(RuntimeError, match="mid-gen error"):
        collect_async(partial_gen())
    assert len(error_received) == 1
    assert isinstance(error_received[0], RuntimeError)
    assert after_fired == []


# ---------------------------------------------------------------------------
# AFTER_INVOKE fires on early break (AsyncGenTool)
# ---------------------------------------------------------------------------


def test_after_invoke_fires_with_partial_list_on_early_break():
    """AFTER_INVOKE fires with partial collected values when caller breaks early."""
    received = []

    @tool()
    async def three_values():
        yield 10
        yield 20
        yield 30

    @three_values.after_invoke
    async def capture_partial_break(values: list) -> None:
        received.append(list(values))

    async def run_break():
        async for v in three_values():
            if v == 10:
                break  # stop after first value

    asyncio.run(run_break())
    assert received == [[10]]


# ---------------------------------------------------------------------------
# Lock behaviour: lock covers only fn, AFTER_INVOKE is outside lock
# ---------------------------------------------------------------------------


def test_lock_covers_fn_not_after_invoke():
    """With lock=True, the lock is released before AFTER_INVOKE runs, so a second
    concurrent call can acquire the lock while the first call's AFTER_INVOKE is
    still executing."""
    order = []

    @tool(lock=True)
    async def locked_tool(x: int) -> int:
        order.append(("fn_start", x))
        await asyncio.sleep(0.02)
        order.append(("fn_end", x))
        return x

    @locked_tool.after_invoke
    async def slow_after(result: int) -> None:
        order.append(("after_start", result))
        await asyncio.sleep(0.05)
        order.append(("after_end", result))

    async def run():
        await asyncio.gather(locked_tool(1), locked_tool(2))

    asyncio.run(run())
    # fn calls must be serialized (no overlap between call 1 and call 2's fn)
    fn1_end = order.index(("fn_end", 1))
    fn2_start = order.index(("fn_start", 2))
    assert fn1_end < fn2_start
    # after_invoke of call 1 must start AFTER fn_end of call 1
    after1_start = order.index(("after_start", 1))
    assert fn1_end < after1_start
    # fn_start of call 2 must come BEFORE after_end of call 1 (lock released before after)
    after1_end = order.index(("after_end", 1))
    assert fn2_start < after1_end


# ---------------------------------------------------------------------------
# Tag system tests
# ---------------------------------------------------------------------------


def test_tool_tags_set_from_decorator():
    """@tool(tags=[...]) stores the frozenset on the instance."""

    @tool(tags=["foo", "bar"])
    async def tagged_tool() -> None:
        pass

    assert tagged_tool.tags == frozenset({"foo", "bar"})


def test_tool_no_tags_gives_empty_frozenset():
    """@tool without tags gives an empty frozenset."""

    @tool()
    async def untagged_tool() -> None:
        pass

    assert untagged_tool.tags == frozenset()


def test_global_hook_with_tags_fires_only_for_matching_tools():
    """A global hook with tags only fires for tools that share at least one tag."""
    from pygents.hooks import hook
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    fired_for = []

    @tool(tags=["foo"])
    async def foo_tool() -> int:
        return 1

    @tool(tags=["bar"])
    async def bar_tool() -> int:
        return 2

    @hook(ToolHook.AFTER_INVOKE, tags={"foo"})
    async def foo_only_hook(result: int) -> None:
        fired_for.append(result)

    asyncio.run(foo_tool())
    asyncio.run(bar_tool())
    assert fired_for == [1]  # fired only for foo_tool


def test_global_hook_without_tags_fires_for_all_tools():
    """A global hook without tags fires for all tools regardless of their tags."""
    from pygents.hooks import hook
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    fired_for = []

    @tool(tags=["alpha"])
    async def alpha_tool() -> int:
        return 10

    @tool()
    async def no_tag_tool() -> int:
        return 20

    @hook(ToolHook.AFTER_INVOKE)
    async def fires_for_all(result: int) -> None:
        fired_for.append(result)

    asyncio.run(alpha_tool())
    asyncio.run(no_tag_tool())
    assert fired_for == [10, 20]


def test_global_hook_with_tags_does_not_fire_for_untagged_tool():
    """A tagged global hook does not fire for a tool with no tags."""
    from pygents.hooks import hook
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    fired = []

    @tool()
    async def untagged_for_tag_test() -> int:
        return 99

    @hook(ToolHook.AFTER_INVOKE, tags={"special"})
    async def special_hook(result: int) -> None:
        fired.append(result)

    asyncio.run(untagged_for_tag_test())
    assert fired == []


def test_subtool_registered_and_attached_to_parent():
    @tool()
    async def parent_subtool_reg() -> None:
        """Parent for subtool registration test."""
        pass

    @parent_subtool_reg.subtool()
    async def child_subtool_reg() -> str:
        """Child subtool."""
        return "ok"

    registered = ToolRegistry.get("parent_subtool_reg.child_subtool_reg")
    assert registered is child_subtool_reg
    with pytest.raises(UnregisteredToolError):
        ToolRegistry.get("child_subtool_reg")
    assert len(parent_subtool_reg._subtools) == 1
    assert parent_subtool_reg._subtools[0] is child_subtool_reg
    assert asyncio.run(cast(Coroutine[Any, Any, str], child_subtool_reg())) == "ok"


def test_subtool_multiple_preserve_order():
    @tool()
    async def parent_subtool_order() -> None:
        pass

    @parent_subtool_order.subtool()
    async def first_subtool_order() -> str:
        return "first"

    @parent_subtool_order.subtool()
    async def second_subtool_order() -> str:
        return "second"

    assert [t.metadata.name for t in parent_subtool_order._subtools] == [
        "first_subtool_order",
        "second_subtool_order",
    ]
    assert ToolRegistry.get("parent_subtool_order.first_subtool_order") is first_subtool_order
    assert ToolRegistry.get("parent_subtool_order.second_subtool_order") is second_subtool_order


def test_doc_tree_no_subtools():
    @tool()
    async def leaf_doc_tree() -> None:
        """A leaf tool."""
        pass

    tree = leaf_doc_tree.doc_tree()
    assert tree == {
        "name": "leaf_doc_tree",
        "description": "A leaf tool.",
        "subtools": [],
    }
    assert "start_time" not in tree
    assert "end_time" not in tree


def test_doc_tree_with_subtools_recursive():
    @tool()
    async def root_doc_tree() -> None:
        """Root tool."""
        pass

    @root_doc_tree.subtool()
    async def child_a_doc_tree() -> None:
        """Child A."""
        pass

    @root_doc_tree.subtool()
    async def child_b_doc_tree() -> None:
        """Child B."""
        pass

    tree = root_doc_tree.doc_tree()
    assert tree["name"] == "root_doc_tree"
    assert tree["description"] == "Root tool."
    assert len(tree["subtools"]) == 2
    assert tree["subtools"][0] == {
        "name": "child_a_doc_tree",
        "description": "Child A.",
        "subtools": [],
    }
    assert tree["subtools"][1] == {
        "name": "child_b_doc_tree",
        "description": "Child B.",
        "subtools": [],
    }


def test_subtool_with_lock():
    @tool()
    async def parent_subtool_lock() -> None:
        pass

    @parent_subtool_lock.subtool(lock=True)
    async def locked_subtool_lock() -> int:
        return 42

    assert locked_subtool_lock.lock is not None
    assert asyncio.run(cast(Coroutine[Any, Any, int], locked_subtool_lock())) == 42


def test_subtool_invocation_smoke():
    @tool()
    async def parent_invoke_smoke() -> None:
        pass

    @parent_invoke_smoke.subtool()
    async def add_invoke_smoke(a: int, b: int) -> int:
        return a + b

    result = asyncio.run(cast(Coroutine[Any, Any, int], add_invoke_smoke(2, 3)))
    assert result == 5


def test_subtool_sync_function_raises_type_error():
    @tool()
    async def parent_sync_reject() -> None:
        pass

    with pytest.raises(TypeError, match="Tool must be async"):

        @parent_sync_reject.subtool()
        def sync_child_sync_reject() -> str:
            return "no"


def test_subtool_nested_registry_key_is_full_path():
    @tool()
    async def root_nested() -> None:
        pass

    @root_nested.subtool()
    async def child_nested() -> None:
        pass

    @child_nested.subtool()
    async def grandchild_nested() -> str:
        return "ok"

    assert grandchild_nested.__name__ == "root_nested.child_nested.grandchild_nested"
    assert ToolRegistry.get("root_nested.child_nested.grandchild_nested") is grandchild_nested
    assert asyncio.run(cast(Coroutine[Any, Any, str], grandchild_nested())) == "ok"


def test_subtool_same_short_name_under_different_parents():
    @tool()
    async def parent_a_scope() -> None:
        pass

    @tool()
    async def parent_b_scope() -> None:
        pass

    @parent_a_scope.subtool()
    async def foo_scope() -> str:
        return "a"

    @parent_b_scope.subtool()
    async def foo_scope_b() -> str:
        return "b"

    a_tool = ToolRegistry.get("parent_a_scope.foo_scope")
    b_tool = ToolRegistry.get("parent_b_scope.foo_scope_b")
    assert asyncio.run(cast(Coroutine[Any, Any, str], a_tool())) == "a"
    assert asyncio.run(cast(Coroutine[Any, Any, str], b_tool())) == "b"


def test_tool_registry_definitions_returns_doc_trees_for_root_tools_only():
    @tool()
    async def root_def_a() -> None:
        """Root A."""
        pass

    @root_def_a.subtool()
    async def sub_def_a() -> None:
        """Sub A."""
        pass

    @tool()
    async def root_def_b() -> None:
        """Root B."""
        pass

    defs = ToolRegistry.definitions()
    names = [d["name"] for d in defs]
    assert "root_def_a" in names
    assert "root_def_b" in names
    assert "sub_def_a" not in names
    root_a = next(d for d in defs if d["name"] == "root_def_a")
    assert root_a["description"] == "Root A."
    assert len(root_a["subtools"]) == 1
    assert root_a["subtools"][0]["name"] == "sub_def_a"
    assert root_a["subtools"][0]["description"] == "Sub A."
