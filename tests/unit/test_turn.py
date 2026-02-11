"""
Tests for pygents.turn, driven by the following decision table.

Decision table for pygents/turn.py
----------------------------------
__setattr__:
  S1  _is_running and name not in mutable_while_running -> SafeExecutionError
  S2  Else -> super().__setattr__

__init__:
  I1  tool is str -> self.tool = ToolRegistry.get(tool)
  I2  tool is callable -> self.tool = ToolRegistry.get(tool.__name__)
  I3  args/kwargs/metadata None -> defaults []; {}; {}
  I4  After init: start_time, end_time, stop_reason None; _is_running False; hooks []

returning():
  R1  Already running -> SafeExecutionError (decorator)
  R2  Tool is async gen -> WrongRunMethodError "use yielding()"
  R3  Normal: start_time, BEFORE_RUN, eval_args/kwargs, wait_for(tool), COMPLETED, AFTER_RUN, return output
  R4  Timeout -> TIMEOUT, ON_TIMEOUT, TurnTimeoutError, finally end_time
  R5  Tool raises -> ERROR, ON_ERROR(e), re-raise, finally end_time

yielding():
  Y1  Already running -> SafeExecutionError
  Y2  Tool not async gen -> WrongRunMethodError "use returning()"
  Y3  Normal: BEFORE_RUN, queue, ON_VALUE per item, yield, COMPLETED, AFTER_RUN, output = aggregated
  Y4  Timeout -> ON_TIMEOUT, TurnTimeoutError, finally end_time
  Y5  Tool raises -> ERROR, ON_ERROR(e), finally end_time

to_dict/from_dict:
  D1  to_dict: tool_name, args/kwargs evaluated, metadata, timeout, start/end (iso or None), stop_reason.value, output, hooks
  D2  from_dict: restore turn, start/end from iso, StopReason, output, hooks via HookRegistry.get
"""

import asyncio

import pytest

from pygents.errors import SafeExecutionError, TurnTimeoutError, WrongRunMethodError
from pygents.hooks import TurnHook, hook
from pygents.tool import tool
from pygents.turn import StopReason, Turn


@tool()
async def turn_run_sync(x: int) -> int:
    return x + 1


@tool()
async def turn_run_async(x: int) -> int:
    return x + 2


@tool()
async def turn_run_async_gen():
    yield 1
    yield 2


@tool()
async def turn_run_async_gen_20():
    yield 10
    yield 20


@tool()
async def turn_run_async_gen_with_arg(x: int):
    yield x
    yield x + 1


@tool()
async def turn_run_with_args(a: int, b: int) -> int:
    return a + b


@tool()
async def turn_run_raises():
    raise ValueError("tool failed")


@tool()
async def turn_run_returns_list() -> list[int]:
    return [1, 2, 3]


@tool()
async def turn_run_reentrant(turn: Turn):
    return await turn.returning()


@tool()
async def turn_run_yielding_raises():
    yield 1
    raise ValueError("yielding tool failed")


@tool()
async def turn_run_yielding_reentrant(turn: Turn):
    yield 1
    async for _ in turn.yielding():
        pass


@tool(lock=True)
async def turn_run_serialized(events: list) -> None:
    events.append("start")
    await asyncio.sleep(0.01)
    events.append("end")


@tool()
async def turn_run_slow(duration: float) -> str:
    await asyncio.sleep(duration)
    return "done"


@tool()
async def turn_run_slow_yielding(duration: float):
    yield 1
    await asyncio.sleep(duration)
    yield 2


async def _collect_async(agen):
    return [x async for x in agen]


def test_returning_async_single_value_returns_output_and_sets_completed():
    turn = Turn[int]("turn_run_sync", kwargs={"x": 5})
    result = asyncio.run(turn.returning())
    assert result == 6
    assert turn.output == 6
    assert turn.stop_reason == StopReason.COMPLETED
    assert turn.start_time is not None
    assert turn.end_time is not None


def test_turn_init_accepts_tool_callable():
    turn = Turn[int](turn_run_sync, kwargs={"x": 3})
    assert turn.tool is turn_run_sync
    result = asyncio.run(turn.returning())
    assert result == 4
    assert turn.output == 4


def test_returning_supports_positional_args_only():
    turn = Turn[int]("turn_run_with_args", args=[2, 3])
    result = asyncio.run(turn.returning())
    assert result == 5
    assert turn.output == 5
    assert turn.stop_reason == StopReason.COMPLETED


def test_returning_supports_positional_and_keyword_args():
    turn = Turn[int]("turn_run_with_args", args=[2], kwargs={"b": 3})
    result = asyncio.run(turn.returning())
    assert result == 5
    assert turn.output == 5
    assert turn.stop_reason == StopReason.COMPLETED


def test_returning_async_when_tool_raises_sets_stop_reason_error_and_propagates():
    turn = Turn("turn_run_raises", kwargs={})
    with pytest.raises(ValueError, match="tool failed"):
        asyncio.run(turn.returning())
    assert turn.stop_reason == StopReason.ERROR
    assert turn.end_time is not None


def test_returning_async_reentrant_raises_safe_execution_error():
    turn = Turn("turn_run_reentrant", kwargs={"turn": None})
    turn.kwargs["turn"] = turn
    with pytest.raises(SafeExecutionError, match="running"):
        asyncio.run(turn.returning())


def test_turn_setattr_raises_while_running():
    turn = Turn("turn_run_slow", kwargs={"duration": 1.0}, timeout=5)

    async def run_turn():
        await turn.returning()

    async def main():
        task = asyncio.create_task(run_turn())
        await asyncio.sleep(0.05)
        try:
            with pytest.raises(SafeExecutionError, match="Cannot change property .* while the turn is running"):
                turn.timeout = 99
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(main())


def test_returning_async_returning_list_works():
    turn = Turn[list[int]]("turn_run_returns_list", kwargs={})
    result = asyncio.run(turn.returning())
    assert result == [1, 2, 3]
    assert turn.output == [1, 2, 3]
    assert turn.stop_reason == StopReason.COMPLETED


def test_returning_async_rejects_async_gen_tool():
    turn = Turn("turn_run_async_gen", kwargs={})
    with pytest.raises(WrongRunMethodError, match="yielding\\(\\)"):
        asyncio.run(turn.returning())


def test_yielding_async_yields_and_sets_aggregated_output():
    turn = Turn("turn_run_async_gen_20", kwargs={})
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [10, 20]
    assert turn.output == [10, 20]
    assert turn.stop_reason == StopReason.COMPLETED
    assert turn.start_time is not None
    assert turn.end_time is not None


def test_yielding_async_when_tool_raises_sets_stop_reason_error_and_propagates():
    turn = Turn("turn_run_yielding_raises", kwargs={})
    with pytest.raises(ValueError, match="yielding tool failed"):
        asyncio.run(_collect_async(turn.yielding()))
    assert turn.stop_reason == StopReason.ERROR
    assert turn.end_time is not None


def test_yielding_async_reentrant_raises_safe_execution_error():
    turn = Turn("turn_run_yielding_reentrant", kwargs={"turn": None})
    turn.kwargs["turn"] = turn
    with pytest.raises(SafeExecutionError, match="running"):
        asyncio.run(_collect_async(turn.yielding()))


def test_turn_before_run_hook_called():
    events = []

    @hook(TurnHook.BEFORE_RUN)
    async def before_run(turn):
        events.append(("before_run", id(turn)))

    turn = Turn("turn_run_sync", kwargs={"x": 10})
    turn.hooks.append(before_run)
    asyncio.run(turn.returning())
    assert len(events) == 1
    assert events[0][0] == "before_run"
    assert events[0][1] == id(turn)
    assert turn.output == 11


def test_turn_after_run_hook_called():
    events = []

    @hook(TurnHook.AFTER_RUN)
    async def after_run(turn):
        events.append(("after_run", turn.output))

    turn = Turn("turn_run_sync", kwargs={"x": 10})
    turn.hooks.append(after_run)
    asyncio.run(turn.returning())
    assert len(events) == 1
    assert events[0][0] == "after_run"
    assert events[0][1] == 11


def test_turn_on_timeout_hook_called():
    events = []

    @hook(TurnHook.ON_TIMEOUT)
    async def on_timeout(turn):
        events.append("on_timeout")

    turn = Turn("turn_run_slow", kwargs={"duration": 2.0}, timeout=1)
    turn.hooks.append(on_timeout)
    with pytest.raises(TurnTimeoutError):
        asyncio.run(turn.returning())
    assert events == ["on_timeout"]


def test_turn_on_error_hook_called():
    events = []

    @hook(TurnHook.ON_ERROR)
    async def on_error(turn, exc):
        events.append(("on_error", type(exc).__name__, str(exc)))

    turn = Turn("turn_run_raises", kwargs={})
    turn.hooks.append(on_error)
    with pytest.raises(ValueError, match="tool failed"):
        asyncio.run(turn.returning())
    assert len(events) == 1
    assert events[0][0] == "on_error"
    assert events[0][1] == "ValueError"
    assert "tool failed" in events[0][2]


def test_turn_on_value_hook_called_for_each_yield():
    values_seen = []

    @hook(TurnHook.ON_VALUE)
    async def on_value(turn, value):
        values_seen.append(value)

    turn = Turn("turn_run_async_gen_20", kwargs={})
    turn.hooks.append(on_value)
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [10, 20]
    assert values_seen == [10, 20]


def test_yielding_async_rejects_coroutine_tool():
    turn = Turn("turn_run_sync", kwargs={"x": 5})
    with pytest.raises(WrongRunMethodError, match="returning\\(\\)"):
        asyncio.run(_collect_async(turn.yielding()))


def test_concurrent_same_tool_turns_serialize():
    events = []
    turn_a = Turn("turn_run_serialized", kwargs={"events": events})
    turn_b = Turn("turn_run_serialized", kwargs={"events": events})

    async def run_both():
        await asyncio.gather(turn_a.returning(), turn_b.returning())

    asyncio.run(run_both())
    assert len(events) == 4
    assert events == ["start", "end", "start", "end"]


def test_returning_times_out_sets_stop_reason_and_end_time():
    turn = Turn("turn_run_slow", kwargs={"duration": 2.0}, timeout=1)
    with pytest.raises(TurnTimeoutError, match="timed out after 1s"):
        asyncio.run(turn.returning())
    assert turn.stop_reason == StopReason.TIMEOUT
    assert turn.end_time is not None


def test_yielding_times_out_sets_stop_reason_and_end_time():
    turn = Turn("turn_run_slow_yielding", kwargs={"duration": 2.0}, timeout=1)
    with pytest.raises(TurnTimeoutError, match="timed out after 1s"):
        asyncio.run(_collect_async(turn.yielding()))
    assert turn.stop_reason == StopReason.TIMEOUT
    assert turn.end_time is not None


def test_returning_evaluates_callable_kwargs_at_runtime():
    turn = Turn[int]("turn_run_sync", kwargs={"x": lambda: 100})
    result = asyncio.run(turn.returning())
    assert result == 101
    assert turn.output == 101


def test_yielding_evaluates_callable_kwargs_at_runtime():
    turn = Turn("turn_run_async_gen_with_arg", kwargs={"x": lambda: 7})
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [7, 8]
    assert turn.output == [7, 8]


def test_turn_accepts_metadata():
    turn = Turn("turn_run_sync", kwargs={"x": 1}, metadata={"source": "test", "id": 42})
    assert turn.metadata == {"source": "test", "id": 42}
    asyncio.run(turn.returning())
    assert turn.output == 2


def test_turn_metadata_default_empty():
    turn = Turn("turn_run_sync", kwargs={"x": 1})
    assert turn.metadata == {}


def test_turn_to_dict_before_run():
    turn = Turn("turn_run_sync", kwargs={"x": 1}, metadata={"k": "v"}, timeout=30)
    data = turn.to_dict()
    assert data["tool_name"] == "turn_run_sync"
    assert data["kwargs"] == {"x": 1}
    assert data["metadata"] == {"k": "v"}
    assert data["timeout"] == 30
    assert data["start_time"] is None
    assert data["end_time"] is None
    assert data["stop_reason"] is None
    assert data["output"] is None


def test_turn_to_dict_roundtrip():
    turn = Turn("turn_run_sync", kwargs={"x": 10}, metadata={"key": "value"}, timeout=30)
    asyncio.run(turn.returning())
    data = turn.to_dict()
    assert data["tool_name"] == "turn_run_sync"
    assert data["kwargs"] == {"x": 10}
    assert data["metadata"] == {"key": "value"}
    assert data["timeout"] == 30
    assert data["output"] == 11
    assert data["stop_reason"] == "completed"
    assert "start_time" in data and data["start_time"] is not None
    assert "end_time" in data and data["end_time"] is not None

    restored = Turn.from_dict(data)
    assert restored.tool.metadata.name == turn.tool.metadata.name
    assert restored.kwargs == turn.kwargs
    assert restored.metadata == turn.metadata
    assert restored.timeout == turn.timeout
    assert restored.output == turn.output
    assert restored.start_time == turn.start_time
    assert restored.end_time == turn.end_time
    assert restored.stop_reason == turn.stop_reason
    assert restored.tool is turn.tool


def test_turn_to_dict_and_from_dict_with_args_roundtrip():
    turn = Turn("turn_run_with_args", args=[2, 3], kwargs={}, metadata={"key": "value"}, timeout=10)
    asyncio.run(turn.returning())
    data = turn.to_dict()
    assert data["args"] == [2, 3]
    restored = Turn.from_dict(data)
    assert restored.args == [2, 3]
    assert restored.kwargs == {}
    assert restored.metadata == {"key": "value"}
    assert restored.timeout == 10


def test_turn_from_dict_minimal():
    data = {"tool_name": "turn_run_sync", "kwargs": {"x": 5}}
    turn = Turn.from_dict(data)
    assert turn.tool.metadata.name == "turn_run_sync"
    assert turn.kwargs == {"x": 5}
    assert turn.metadata == {}
    assert turn.timeout == 60
    assert turn.start_time is None
    assert turn.end_time is None
    assert turn.stop_reason is None
    assert turn.output is None
    result = asyncio.run(turn.returning())
    assert result == 6
