import asyncio

import pytest

from pygents.errors import SafeExecutionError, TurnTimeoutError, WrongRunMethodError
from pygents.hooks import TurnHook
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
    turn = Turn[int]("turn_run_sync", {"x": 5})
    result = asyncio.run(turn.returning())
    assert result == 6
    assert turn.output == 6
    assert turn.stop_reason == StopReason.COMPLETED
    assert turn.start_time is not None
    assert turn.end_time is not None


def test_returning_async_async_tool_returns_output():
    turn = Turn[int]("turn_run_async", {"x": 5})
    result = asyncio.run(turn.returning())
    assert result == 7
    assert turn.output == 7
    assert turn.stop_reason == StopReason.COMPLETED


def test_returning_async_when_tool_raises_sets_stop_reason_error_and_propagates():
    turn = Turn("turn_run_raises", {})
    with pytest.raises(ValueError, match="tool failed"):
        asyncio.run(turn.returning())
    assert turn.stop_reason == StopReason.ERROR
    assert turn.end_time is not None


def test_returning_async_reentrant_raises_safe_execution_error():
    turn = Turn("turn_run_reentrant", {"turn": None})
    turn.kwargs["turn"] = turn
    with pytest.raises(SafeExecutionError, match="running"):
        asyncio.run(turn.returning())


def test_returning_async_returning_list_works():
    turn = Turn[list[int]]("turn_run_returns_list", {})
    result = asyncio.run(turn.returning())
    assert result == [1, 2, 3]
    assert turn.output == [1, 2, 3]
    assert turn.stop_reason == StopReason.COMPLETED


def test_returning_async_sets_end_time_in_finally_on_error():
    turn = Turn("turn_run_raises", {})
    with pytest.raises(ValueError):
        asyncio.run(turn.returning())
    assert turn.end_time is not None


def test_returning_async_rejects_async_gen_tool():
    turn = Turn("turn_run_async_gen", {})
    with pytest.raises(WrongRunMethodError, match="yielding\\(\\)"):
        asyncio.run(turn.returning())


def test_yielding_async_yields_and_sets_aggregated_output():
    turn = Turn("turn_run_async_gen_20", {})
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [10, 20]
    assert turn.output == [10, 20]
    assert turn.stop_reason == StopReason.COMPLETED
    assert turn.start_time is not None
    assert turn.end_time is not None


def test_yielding_async_async_gen_yields_and_sets_aggregated_output():
    turn = Turn("turn_run_async_gen", {})
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [1, 2]
    assert turn.output == [1, 2]
    assert turn.stop_reason == StopReason.COMPLETED
    assert turn.end_time is not None


def test_yielding_async_when_tool_raises_sets_stop_reason_error_and_propagates():
    turn = Turn("turn_run_yielding_raises", {})
    with pytest.raises(ValueError, match="yielding tool failed"):
        asyncio.run(_collect_async(turn.yielding()))
    assert turn.stop_reason == StopReason.ERROR
    assert turn.end_time is not None


def test_yielding_async_reentrant_raises_safe_execution_error():
    turn = Turn("turn_run_yielding_reentrant", {"turn": None})
    turn.kwargs["turn"] = turn
    with pytest.raises(SafeExecutionError, match="running"):
        asyncio.run(_collect_async(turn.yielding()))


def test_turn_before_run_hook_called():
    events = []

    async def before_run(turn):
        events.append(("before_run", turn.uuid))

    turn = Turn("turn_run_sync", {"x": 10})
    turn.hooks[TurnHook.BEFORE_RUN] = [before_run]
    asyncio.run(turn.returning())
    assert len(events) == 1
    assert events[0][0] == "before_run"
    assert events[0][1] == turn.uuid
    assert turn.output == 11


def test_yielding_async_rejects_coroutine_tool():
    turn = Turn("turn_run_sync", {"x": 5})
    with pytest.raises(WrongRunMethodError, match="returning\\(\\)"):
        asyncio.run(_collect_async(turn.yielding()))


def test_concurrent_same_tool_turns_serialize():
    events = []
    turn_a = Turn("turn_run_serialized", {"events": events})
    turn_b = Turn("turn_run_serialized", {"events": events})

    async def run_both():
        await asyncio.gather(turn_a.returning(), turn_b.returning())

    asyncio.run(run_both())
    assert len(events) == 4
    assert events == ["start", "end", "start", "end"]


def test_returning_times_out_sets_stop_reason_and_end_time():
    turn = Turn("turn_run_slow", {"duration": 2.0}, timeout=1)
    with pytest.raises(TurnTimeoutError, match="timed out after 1s"):
        asyncio.run(turn.returning())
    assert turn.stop_reason == StopReason.TIMEOUT
    assert turn.end_time is not None


def test_yielding_times_out_sets_stop_reason_and_end_time():
    turn = Turn("turn_run_slow_yielding", {"duration": 2.0}, timeout=1)
    with pytest.raises(TurnTimeoutError, match="timed out after 1s"):
        asyncio.run(_collect_async(turn.yielding()))
    assert turn.stop_reason == StopReason.TIMEOUT
    assert turn.end_time is not None


def test_returning_evaluates_callable_kwargs_at_runtime():
    turn = Turn[int]("turn_run_sync", {"x": lambda: 100})
    result = asyncio.run(turn.returning())
    assert result == 101
    assert turn.output == 101


def test_yielding_evaluates_callable_kwargs_at_runtime():
    turn = Turn("turn_run_async_gen_with_arg", {"x": lambda: 7})
    items = asyncio.run(_collect_async(turn.yielding()))
    assert items == [7, 8]
    assert turn.output == [7, 8]


def test_turn_accepts_metadata():
    turn = Turn("turn_run_sync", {"x": 1}, metadata={"source": "test", "id": 42})
    assert turn.metadata == {"source": "test", "id": 42}
    asyncio.run(turn.returning())
    assert turn.output == 2


def test_turn_metadata_default_empty():
    turn = Turn("turn_run_sync", {"x": 1})
    assert turn.metadata == {}


def test_turn_to_dict_roundtrip():
    turn = Turn("turn_run_sync", {"x": 10}, metadata={"key": "value"}, timeout=30)
    asyncio.run(turn.returning())
    data = turn.to_dict()
    assert data["uuid"] == turn.uuid
    assert data["tool_name"] == "turn_run_sync"
    assert data["kwargs"] == {"x": 10}
    assert data["metadata"] == {"key": "value"}
    assert data["timeout"] == 30
    assert data["output"] == 11
    assert data["stop_reason"] == "completed"
    assert "start_time" in data and data["start_time"] is not None
    assert "end_time" in data and data["end_time"] is not None

    restored = Turn.from_dict(data)
    assert restored.uuid == turn.uuid
    assert restored.tool.metadata.name == turn.tool.metadata.name
    assert restored.kwargs == turn.kwargs
    assert restored.metadata == turn.metadata
    assert restored.timeout == turn.timeout
    assert restored.output == turn.output
    assert restored.start_time == turn.start_time
    assert restored.end_time == turn.end_time
    assert restored.stop_reason == turn.stop_reason
    assert restored.tool is turn.tool


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
    assert turn.uuid is not None
    result = asyncio.run(turn.returning())
    assert result == 6
