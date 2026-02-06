import asyncio

import pytest

from app.enums import StopReason, ToolType
from app.errors import SafeExecutionError, WrongRunMethodError
from app.tool import tool
from app.turn import Turn


@tool(type=ToolType.ACTION)
async def turn_run_sync(x: int) -> int:
    return x + 1


@tool(type=ToolType.ACTION)
async def turn_run_async(x: int) -> int:
    return x + 2


@tool(type=ToolType.ACTION)
async def turn_run_async_gen():
    yield 1
    yield 2


@tool(type=ToolType.ACTION)
async def turn_run_async_gen_20():
    yield 10
    yield 20


@tool(type=ToolType.ACTION)
async def turn_run_raises():
    raise ValueError("tool failed")


@tool(type=ToolType.ACTION)
async def turn_run_returns_list() -> list[int]:
    return [1, 2, 3]


@tool(type=ToolType.ACTION)
async def turn_run_reentrant(turn: Turn):
    return await turn.returning()


@tool(type=ToolType.ACTION)
async def turn_run_yielding_raises():
    yield 1
    raise ValueError("yielding tool failed")


@tool(type=ToolType.ACTION)
async def turn_run_yielding_reentrant(turn: Turn):
    yield 1
    async for _ in turn.yielding():
        pass


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


def test_yielding_async_rejects_coroutine_tool():
    turn = Turn("turn_run_sync", {"x": 5})
    with pytest.raises(WrongRunMethodError, match="returning\\(\\)"):
        asyncio.run(_collect_async(turn.yielding()))
