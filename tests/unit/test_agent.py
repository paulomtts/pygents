"""
Tests for pygents.agent, driven by the following decision table.

Decision table for pygents/agent.py
-----------------------------------
__setattr__:
  S1  _is_running and name not in (_is_running, _current_turn) -> SafeExecutionError
  S2  Else -> super().__setattr__

__init__:
  I1  Tool not in ToolRegistry -> UnregisteredToolError (from get)
  I2  Tool in registry but different instance -> ValueError "not the instance given"
  I3  Ok: name, description, tools, _tool_names, _queue, hooks [], _is_running False, _current_turn None; AgentRegistry.register
  I4  context_queue=None -> default ContextQueue(limit=10) created
  I5  context_queue=<instance> -> used as-is

put(turn):
  P1  turn.tool is None -> ValueError "Turn has no tool"
  P2  turn.tool.metadata.name not in _tool_names -> ValueError "does not accept tool"
  P3  Else: BEFORE_PUT, put_nowait, AFTER_PUT

send_turn(agent_name, turn):
  T1  AgentRegistry.get(agent_name) raises -> that exception
  T2  Else -> await target.put(turn)

run():
  R1  Already running -> SafeExecutionError
  R2  Loop: get turn from _current_turn or queue; BEFORE_TURN
  R3  Async gen tool -> yielding(), ON_TURN_VALUE per value, yield (turn, value)
  R4  Coroutine tool -> returning(), ON_TURN_VALUE, yield (turn, output)
  R5  TurnTimeoutError -> ON_TURN_TIMEOUT, re-raise
  R6  Other exception -> ON_TURN_ERROR, re-raise
  R7  After: AFTER_TURN; if turn.output is Turn -> put(turn.output); clear _current_turn
  R8  finally: _is_running False, _current_turn None

branch(name, description=None, tools=None, hooks=...):
  B1  description None -> child inherits self.description
  B2  description set -> child uses that
  B3  tools None -> child inherits self.tools
  B4  tools set -> child uses that
  B5  hooks is ... -> child inherits self.hooks
  B6  hooks=[] or hooks=[...] -> child uses that list
  B7  Queue copied to child (non-destructive)
  B8  Child is independent after creation
  B9  Child is registered in AgentRegistry
  B10 child's context_queue is a branch of the parent's

_queue_snapshot: non-destructive peek. to_dict/from_dict: queue, current_turn, hooks.
"""

import asyncio

import pytest

from pygents.errors import (
    SafeExecutionError,
    TurnTimeoutError,
    UnregisteredAgentError,
    UnregisteredToolError,
)
from pygents.hooks import AgentHook, ContextPoolHook, hook
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import Turn
from pygents.agent import Agent
from pygents.context_queue import ContextQueue


@tool()
async def add_agent(a: int, b: int) -> int:
    return a + b


@tool()
async def slow_tool_agent(duration: float) -> str:
    await asyncio.sleep(duration)
    return "done"


@tool()
async def stream_agent():
    yield 1
    yield 2
    yield 3


@tool()
async def returns_turn_agent(turn: Turn) -> Turn:
    return turn


@tool()
async def raising_tool_agent() -> None:
    raise ValueError("tool error")


@tool()
async def returns_context_item_no_id():
    from pygents.context_pool import ContextItem
    return ContextItem(content=42)


@tool()
async def returns_context_item_with_id():
    from pygents.context_pool import ContextItem
    return ContextItem(content=99, description="result", id="x")


# ---------------------------------------------------------------------------
# I1–I3 – __init__
# ---------------------------------------------------------------------------


def test_agent_rejects_tool_not_in_registry():
    unregistered = type(
        "FakeTool",
        (),
        {"metadata": type("M", (), {"name": "unregistered_tool"})()},
    )()
    with pytest.raises(UnregisteredToolError, match="not found"):
        Agent("a", "desc", [unregistered])


def test_agent_init_rejects_tool_wrong_instance():
    AgentRegistry.clear()
    Agent("a", "desc", [add_agent])
    fake_same_name = type(
        "FakeTool",
        (),
        {"metadata": type("M", (), {"name": "add_agent"})(), "fn": None},
    )()
    with pytest.raises(ValueError, match="not the instance given"):
        Agent("b", "desc", [fake_same_name])


def test_agent_init_accepts_context_pool_instance():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(ContextPoolHook.BEFORE_ADD)
    async def pool_hook(pool, item):
        pass

    from pygents.context_pool import ContextPool
    agent = Agent("a", "desc", [add_agent], context_pool=ContextPool(hooks=[pool_hook]))
    assert pool_hook in agent.context_pool.hooks


def test_agent_init_default_pool_when_none_provided():
    AgentRegistry.clear()
    from pygents.context_pool import ContextPool
    agent = Agent("a", "desc", [add_agent])
    assert isinstance(agent.context_pool, ContextPool)
    assert len(agent.context_pool) == 0


def test_agent_init_uses_provided_pool_instance():
    AgentRegistry.clear()
    from pygents.context_pool import ContextPool
    pool = ContextPool(limit=5)
    agent = Agent("a", "desc", [add_agent], context_pool=pool)
    assert agent.context_pool is pool


def test_agent_branch_child_pool_inherits_hooks():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(ContextPoolHook.AFTER_ADD)
    async def parent_pool_hook(pool, item):
        pass

    from pygents.context_pool import ContextPool
    parent = Agent("parent", "desc", [add_agent], context_pool=ContextPool(hooks=[parent_pool_hook]))
    child = parent.branch("child")
    assert parent_pool_hook in child.context_pool.hooks


def test_agent_init_accepts_context_queue_instance():
    AgentRegistry.clear()
    queue = ContextQueue(limit=5)
    agent = Agent("a", "desc", [add_agent], context_queue=queue)
    assert agent.context_queue is queue


def test_agent_init_default_queue_when_none_provided():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    assert isinstance(agent.context_queue, ContextQueue)
    assert agent.context_queue.limit == 10


def test_agent_init_uses_provided_queue_instance():
    AgentRegistry.clear()
    queue = ContextQueue(limit=20)
    agent = Agent("a", "desc", [add_agent], context_queue=queue)
    assert agent.context_queue is queue


def test_agent_branch_child_queue_inherits_hooks():
    AgentRegistry.clear()
    queue = ContextQueue(limit=5)
    parent = Agent("parent", "desc", [add_agent], context_queue=queue)
    child = parent.branch("child")
    assert child.context_queue is not parent.context_queue
    assert child.context_queue.limit == parent.context_queue.limit


def test_agent_add_context_routes_to_queue_when_id_is_none():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [returns_context_item_no_id])

    async def run():
        await agent.put(Turn("returns_context_item_no_id", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert len(agent.context_queue) == 1
    assert len(agent.context_pool) == 0
    assert agent.context_queue.items[0].content == 42


def test_agent_add_context_routes_to_pool_when_id_provided():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [returns_context_item_with_id])

    async def run():
        await agent.put(Turn("returns_context_item_with_id", kwargs={}))
        async for _ in agent.run():
            pass

    asyncio.run(run())
    assert len(agent.context_pool) == 1
    assert len(agent.context_queue) == 0
    assert agent.context_pool.get("x").content == 99


# ---------------------------------------------------------------------------
# P1–P3 – put()
# ---------------------------------------------------------------------------


def test_put_rejects_turn_with_no_tool():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", kwargs={"a": 1, "b": 2})
    object.__setattr__(turn, "tool", None)
    with pytest.raises(ValueError, match="Turn has no tool"):
        asyncio.run(agent.put(turn))


def test_put_rejects_turn_with_unknown_tool():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", kwargs={"a": 1, "b": 2})
    fake_tool = type("FakeTool", (), {"metadata": type("M", (), {"name": "other_tool"})()})()
    object.__setattr__(turn, "tool", fake_tool)
    with pytest.raises(ValueError, match="does not accept tool"):
        asyncio.run(agent.put(turn))


def test_agent_registered_after_init():
    AgentRegistry.clear()
    agent = Agent("registered_agent", "Desc", [add_agent])
    assert AgentRegistry.get("registered_agent") is agent


def test_put_before_put_and_after_put_hooks_called():
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.BEFORE_PUT)
    async def before_put(agent, turn):
        events.append(("before_put", turn.tool.metadata.name))

    @hook(AgentHook.AFTER_PUT)
    async def after_put(agent, turn):
        events.append(("after_put", turn.tool.metadata.name))

    agent = Agent("a", "desc", [add_agent])
    agent.hooks.extend([before_put, after_put])
    turn = Turn("add_agent", kwargs={"a": 1, "b": 2})
    asyncio.run(agent.put(turn))
    assert events == [("before_put", "add_agent"), ("after_put", "add_agent")]


def test_put_then_run_yields_turn_and_result():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", kwargs={"a": 1, "b": 2})

    async def put_and_run_once():
        await agent.put(turn)
        async for t, v in agent.run():
            return t, v
        return None, None

    result_turn, result_value = asyncio.run(put_and_run_once())
    assert result_value == 3
    assert result_turn is turn
    assert turn.output == 3


# ---------------------------------------------------------------------------
# R1–R8 – run()
# ---------------------------------------------------------------------------


def test_run_processes_turn_and_stops_when_queue_empty():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])

    async def consume():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        async for _ in agent.run():
            pass

    asyncio.run(consume())
    assert agent._queue.empty()


def test_run_streams_turn_results():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])

    async def collect():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        await agent.put(Turn("add_agent", kwargs={"a": 10, "b": 20}))
        items = []
        async for t, v in agent.run():
            items.append((t, v))
        return items

    items = asyncio.run(collect())
    assert len(items) == 2
    assert items[0][1] == 3
    assert items[0][0].output == 3
    assert items[1][1] == 30
    assert items[1][0].output == 30


def test_run_supports_turn_with_positional_args():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])

    async def collect():
        await agent.put(Turn("add_agent", args=[1, 2]))
        items = []
        async for t, v in agent.run():
            items.append((t, v))
        return items

    items = asyncio.run(collect())
    assert len(items) == 1
    assert items[0][1] == 3
    assert items[0][0].output == 3


def test_run_streams_yielding_turn_multiple_values():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [stream_agent, add_agent])

    async def collect():
        await agent.put(Turn("stream_agent", kwargs={}))
        await agent.put(Turn("add_agent", kwargs={"a": 5, "b": 5}))
        items = []
        async for t, v in agent.run():
            items.append((t, v))
        return items

    items = asyncio.run(collect())
    assert len(items) == 4
    assert [v for _, v in items] == [1, 2, 3, 10]
    assert items[0][0].output == [1, 2, 3]


def test_run_propagates_turn_timeout_error():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent])

    async def consume():
        await agent.put(Turn("slow_tool_agent", kwargs={"duration": 2.0}, timeout=1))
        async for _ in agent.run():
            pass

    with pytest.raises(TurnTimeoutError, match="timed out"):
        asyncio.run(consume())


def test_run_reentrant_raises_safe_execution_error():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent])

    async def main():
        await agent.put(Turn("slow_tool_agent", kwargs={"duration": 2.0}))
        await agent.put(Turn("slow_tool_agent", kwargs={"duration": 0.1}))
        runner = asyncio.create_task(_consume(agent.run()))
        await asyncio.sleep(0.05)
        with pytest.raises(SafeExecutionError, match="running"):
            async for _ in agent.run():
                pass
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass

    async def _consume(gen):
        async for _ in gen:
            pass

    asyncio.run(main())


def test_agent_setattr_raises_while_running():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent])

    async def run_agent():
        await agent.put(Turn("slow_tool_agent", kwargs={"duration": 1.0}))
        async for _ in agent.run():
            break

    async def main():
        task = asyncio.create_task(run_agent())
        await asyncio.sleep(0.05)
        try:
            with pytest.raises(SafeExecutionError, match="Cannot change property .* while the agent is running"):
                agent.name = "other"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(main())


def test_run_before_turn_after_turn_and_on_turn_value_hooks_called():
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.BEFORE_TURN)
    async def before_turn(agent):
        events.append("before_turn")

    @hook(AgentHook.ON_TURN_VALUE)
    async def on_turn_value(agent, turn, value):
        events.append(("on_turn_value", value))

    @hook(AgentHook.AFTER_TURN)
    async def after_turn(agent, turn):
        events.append(("after_turn", turn.tool.metadata.name))

    agent = Agent("a", "desc", [add_agent])
    agent.hooks.extend([before_turn, on_turn_value, after_turn])
    asyncio.run(agent.put(Turn("add_agent", kwargs={"a": 2, "b": 3})))

    async def run_and_collect():
        out = []
        async for t, v in agent.run():
            out.append((t, v))
        return out

    items = asyncio.run(run_and_collect())
    assert events == ["before_turn", ("on_turn_value", 5), ("after_turn", "add_agent")]
    assert items[0][1] == 5


def test_run_on_turn_timeout_hook_called():
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_TURN_TIMEOUT)
    async def on_turn_timeout(agent, turn):
        events.append("on_turn_timeout")

    agent = Agent("a", "desc", [slow_tool_agent])
    agent.hooks.append(on_turn_timeout)
    asyncio.run(agent.put(Turn("slow_tool_agent", kwargs={"duration": 2.0}, timeout=1)))

    async def consume():
        async for _ in agent.run():
            pass

    with pytest.raises(TurnTimeoutError):
        asyncio.run(consume())
    assert events == ["on_turn_timeout"]


def test_run_on_turn_error_hook_called():
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_TURN_ERROR)
    async def on_turn_error(agent, turn, exc):
        events.append(("on_turn_error", type(exc).__name__))

    agent = Agent("a", "desc", [raising_tool_agent])
    agent.hooks.append(on_turn_error)
    asyncio.run(agent.put(Turn("raising_tool_agent", kwargs={})))

    async def consume():
        async for _ in agent.run():
            pass

    with pytest.raises(ValueError, match="tool error"):
        asyncio.run(consume())
    assert events == [("on_turn_error", "ValueError")]


def test_run_puts_turn_output_when_turn_returns_turn():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent, returns_turn_agent])
    inner = Turn("add_agent", kwargs={"a": 1, "b": 2})
    outer = Turn("returns_turn_agent", kwargs={"turn": inner})
    asyncio.run(agent.put(outer))

    async def collect():
        out = []
        async for t, v in agent.run():
            out.append((t, v))
        return out

    items = asyncio.run(collect())
    assert len(items) == 2
    assert items[0][0] is outer
    assert items[0][1] is inner
    assert items[1][0] is inner
    assert items[1][1] == 3


# ---------------------------------------------------------------------------
# T1–T2 – send_turn()
# ---------------------------------------------------------------------------


def test_send_turn_enqueues_on_target_agent():
    AgentRegistry.clear()
    agent_a = Agent("alice", "First", [add_agent])
    agent_b = Agent("bob", "Second", [add_agent])

    async def main():
        await agent_a.send_turn("bob", Turn("add_agent", kwargs={"a": 10, "b": 20}))
        await agent_a.send_turn("bob", Turn("add_agent", kwargs={"a": 5, "b": 5}))
        out = []
        async for t, v in agent_b.run():
            out.append((t, v))
        return out

    items = asyncio.run(main())
    assert len(items) == 2
    assert items[0][1] == 30
    assert items[1][1] == 10


def test_send_turn_to_unregistered_agent_raises():
    AgentRegistry.clear()
    agent = Agent("solo", "Only", [add_agent])

    async def main():
        await agent.send_turn("nonexistent", Turn("add_agent", kwargs={"a": 1, "b": 2}))

    with pytest.raises(UnregisteredAgentError, match="not found"):
        asyncio.run(main())


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------


def test_agent_to_dict():
    AgentRegistry.clear()
    agent = Agent("serial", "Serializable agent", [add_agent])
    data = agent.to_dict()
    assert data["name"] == "serial"
    assert data["description"] == "Serializable agent"
    assert data["tool_names"] == ["add_agent"]
    assert data["queue"] == []
    assert "context_queue" in data


def test_agent_to_dict_includes_queued_turns():
    AgentRegistry.clear()
    agent = Agent("q", "With queue", [add_agent])

    async def put_then_serialize():
        await agent.put(Turn("add_agent", kwargs={"a": 2, "b": 3}))
        await agent.put(Turn("add_agent", kwargs={"a": 10, "b": 20}))
        return agent.to_dict()

    data = asyncio.run(put_then_serialize())
    assert len(data["queue"]) == 2
    assert data["queue"][0]["tool_name"] == "add_agent"
    assert data["queue"][0]["kwargs"] == {"a": 2, "b": 3}
    assert data["queue"][1]["tool_name"] == "add_agent"

    async def run_after_snapshot():
        results = []
        async for _, v in agent.run():
            results.append(v)
        return results

    results = asyncio.run(run_after_snapshot())
    assert results == [5, 30]


def test_agent_from_dict_roundtrip():
    AgentRegistry.clear()
    agent = Agent("roundtrip", "Desc", [add_agent])

    async def put_run_serialize():
        await agent.put(Turn("add_agent", kwargs={"a": 5, "b": 10}))
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 1}))
        return agent.to_dict()

    data = asyncio.run(put_run_serialize())
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.name == agent.name
    assert restored.description == agent.description
    assert restored._tool_names == agent._tool_names
    assert restored._queue.qsize() == 2

    async def run_restored():
        results = []
        async for t, v in restored.run():
            results.append((t, v))
        return results

    results = asyncio.run(run_restored())
    assert len(results) == 2
    assert results[0][1] == 15
    assert results[1][1] == 2
    assert isinstance(restored.context_queue, ContextQueue)
    assert restored.context_queue.limit == 10


def test_agent_from_dict_empty_queue():
    AgentRegistry.clear()
    agent = Agent("empty", "No turns", [add_agent])
    data = agent.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.name == "empty"
    assert restored._queue.qsize() == 0


def test_agent_to_dict_includes_current_turn_when_set():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    data = agent.to_dict()
    data["current_turn"] = Turn("add_agent", kwargs={"a": 1, "b": 2}).to_dict()
    data["queue"] = []
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored._current_turn is not None
    assert restored._current_turn.tool.metadata.name == "add_agent"
    serialized = restored.to_dict()
    assert serialized["current_turn"] is not None
    assert serialized["current_turn"]["tool_name"] == "add_agent"


def test_agent_from_dict_with_current_turn_processes_it_first():
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    turn = Turn("add_agent", kwargs={"a": 7, "b": 3})
    data = agent.to_dict()
    data["current_turn"] = turn.to_dict()
    data["queue"] = []
    AgentRegistry.clear()
    restored = Agent.from_dict(data)

    async def run_once():
        async for t, v in restored.run():
            return t, v
        return None, None

    result_turn, result_value = asyncio.run(run_once())
    assert result_value == 10
    assert result_turn.tool.metadata.name == "add_agent"
    assert restored._queue.empty()


# ---------------------------------------------------------------------------
# B1–B9 – branch()
# ---------------------------------------------------------------------------


def test_branch_inherits_defaults():
    AgentRegistry.clear()
    parent = Agent("parent", "Parent agent", [add_agent])
    child = parent.branch("child")
    assert child.name == "child"
    assert child.description == "Parent agent"
    assert child._tool_names == parent._tool_names


def test_branch_overrides_description():
    AgentRegistry.clear()
    parent = Agent("parent", "Parent agent", [add_agent])
    child = parent.branch("child", description="Child agent")
    assert child.description == "Child agent"


def test_branch_overrides_tools():
    AgentRegistry.clear()
    parent = Agent("parent", "Desc", [add_agent, stream_agent])
    child = parent.branch("child", tools=[add_agent])
    assert child._tool_names == {"add_agent"}


def test_branch_inherits_hooks():
    from pygents.registry import HookRegistry

    AgentRegistry.clear()
    HookRegistry.clear()

    @hook(AgentHook.BEFORE_TURN)
    async def branch_before_turn(agent):
        pass

    parent = Agent("parent", "Desc", [add_agent])
    parent.hooks.append(branch_before_turn)
    child = parent.branch("child")
    assert branch_before_turn in child.hooks


def test_branch_overrides_hooks():
    AgentRegistry.clear()

    @hook(AgentHook.BEFORE_TURN)
    async def parent_hook(agent):
        pass

    parent = Agent("parent", "Desc", [add_agent])
    parent.hooks.append(parent_hook)
    child = parent.branch("child", hooks=[])
    assert child.hooks == []


def test_branch_inherits_queue():
    AgentRegistry.clear()
    parent = Agent("parent", "Desc", [add_agent])

    async def branch_and_run():
        await parent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        await parent.put(Turn("add_agent", kwargs={"a": 10, "b": 20}))
        child = parent.branch("child")
        assert child._queue.qsize() == 2
        # parent queue is unaffected
        assert parent._queue.qsize() == 2
        results = []
        async for _, v in child.run():
            results.append(v)
        return results

    results = asyncio.run(branch_and_run())
    assert results == [3, 30]


def test_branch_is_independent():
    AgentRegistry.clear()
    parent = Agent("parent", "Desc", [add_agent])

    async def main():
        await parent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        child = parent.branch("child")
        await child.put(Turn("add_agent", kwargs={"a": 100, "b": 200}))
        assert child._queue.qsize() == 2
        assert parent._queue.qsize() == 1

    asyncio.run(main())


def test_branch_is_registered():
    AgentRegistry.clear()
    parent = Agent("parent", "Desc", [add_agent])
    child = parent.branch("child")
    assert AgentRegistry.get("child") is child


def test_branch_empty_queue():
    AgentRegistry.clear()
    parent = Agent("parent", "Desc", [add_agent])
    child = parent.branch("child")
    assert child._queue.qsize() == 0


# ---------------------------------------------------------------------------
# PR1–PR20 – pause() / resume() / is_paused
# ---------------------------------------------------------------------------


def test_is_paused_false_after_init():
    # PR1
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    assert agent.is_paused is False


def test_pause_sets_is_paused():
    # PR2
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    assert agent.is_paused is True


def test_resume_clears_is_paused():
    # PR3
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    agent.resume()
    assert agent.is_paused is False


def test_pause_before_run_does_not_raise():
    # PR4
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()  # no run() active — must not raise


def test_resume_before_run_does_not_raise():
    # PR5
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.resume()  # already unpaused — must not raise


def test_pause_is_idempotent():
    # PR6
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    agent.pause()
    assert agent.is_paused is True


def test_resume_is_idempotent():
    # PR7
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.resume()
    assert agent.is_paused is False


def test_run_waits_at_gate_when_pre_paused():
    # PR8 – agent paused before run() starts; resumes via external task
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()

    async def main():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))

        async def collect():
            out = []
            async for _, v in agent.run():
                out.append(v)
            return out

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)        # gate should be hit by now
        assert agent.is_paused is True
        agent.resume()
        return await task

    results = asyncio.run(main())
    assert results == [3]


def test_run_pauses_between_turns():
    # PR9 – pause after first turn; second turn waits
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])

    async def main():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 1}))
        await agent.put(Turn("add_agent", kwargs={"a": 2, "b": 2}))

        results = []

        async def collect():
            async for _, v in agent.run():
                results.append(v)
                agent.pause()            # pause after first yield

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)
        assert results == [2]
        assert agent.is_paused is True
        agent.resume()
        await task
        assert results == [2, 4]

    asyncio.run(main())


def test_on_pause_hook_fires_on_pause_entry():
    # PR10
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_PAUSE)
    async def on_pause(agent):
        events.append("on_pause")

    agent = Agent("a", "desc", [add_agent])
    agent.hooks.append(on_pause)
    agent.pause()

    async def main():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        task = asyncio.create_task(_consume(agent.run()))
        await asyncio.sleep(0.05)
        agent.resume()
        await task

    async def _consume(gen):
        async for _ in gen:
            pass

    asyncio.run(main())
    assert events.count("on_pause") == 1


def test_on_resume_hook_fires_on_resume():
    # PR11
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_RESUME)
    async def on_resume(agent):
        events.append("on_resume")

    agent = Agent("a", "desc", [add_agent])
    agent.hooks.append(on_resume)
    agent.pause()

    async def main():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        task = asyncio.create_task(_consume(agent.run()))
        await asyncio.sleep(0.05)
        agent.resume()
        await task

    async def _consume(gen):
        async for _ in gen:
            pass

    asyncio.run(main())
    assert events.count("on_resume") == 1


def test_on_pause_fires_before_before_turn():
    # PR12 – order: ON_PAUSE → ON_RESUME → BEFORE_TURN
    HookRegistry.clear()
    AgentRegistry.clear()
    events = []

    @hook(AgentHook.ON_PAUSE)
    async def on_pause(agent):
        events.append("on_pause")

    @hook(AgentHook.ON_RESUME)
    async def on_resume(agent):
        events.append("on_resume")

    @hook(AgentHook.BEFORE_TURN)
    async def before_turn(agent):
        events.append("before_turn")

    agent = Agent("a", "desc", [add_agent])
    agent.hooks.extend([on_pause, on_resume, before_turn])
    agent.pause()

    async def main():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        task = asyncio.create_task(_consume(agent.run()))
        await asyncio.sleep(0.05)
        agent.resume()
        await task

    async def _consume(gen):
        async for _ in gen:
            pass

    asyncio.run(main())
    assert events == ["on_pause", "on_resume", "before_turn"]


def test_pause_does_not_interrupt_running_turn():
    # PR13 – pause() mid-turn; tool still completes normally
    AgentRegistry.clear()
    agent = Agent("a", "desc", [slow_tool_agent])
    completed = []

    async def main():
        await agent.put(Turn("slow_tool_agent", kwargs={"duration": 0.2}))

        async def collect():
            async for _, v in agent.run():
                completed.append(v)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)        # tool is running
        agent.pause()                    # must not abort the turn
        await task
        assert completed == ["done"]

    asyncio.run(main())


def test_to_dict_is_paused_true_when_paused():
    # PR14
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    assert agent.to_dict()["is_paused"] is True


def test_to_dict_is_paused_false_when_not_paused():
    # PR15
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    assert agent.to_dict()["is_paused"] is False


def test_from_dict_restores_paused_state():
    # PR16
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    data = agent.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.is_paused is True


def test_from_dict_restores_unpaused_state():
    # PR17
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    data = agent.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.is_paused is False


def test_paused_agent_round_trips_and_resumes():
    # PR18 – pause → serialize → restore → resume → runs correctly
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])

    async def put():
        await agent.put(Turn("add_agent", kwargs={"a": 5, "b": 5}))

    asyncio.run(put())
    agent.pause()
    data = agent.to_dict()

    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.is_paused is True

    async def main():
        task = asyncio.create_task(_collect(restored.run()))
        await asyncio.sleep(0.05)
        restored.resume()
        return await task

    async def _collect(gen):
        out = []
        async for _, v in gen:
            out.append(v)
        return out

    results = asyncio.run(main())
    assert results == [10]


def test_setattr_raises_while_paused():
    # PR19 – mutation guard blocks property changes when paused (not just running)
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    with pytest.raises(SafeExecutionError, match="Cannot change property .* while the agent is paused"):
        agent.name = "other"


def test_setattr_allowed_after_resume():
    # PR20 – resume() unblocks the mutation guard
    AgentRegistry.clear()
    agent = Agent("a", "desc", [add_agent])
    agent.pause()
    agent.resume()
    agent.name = "new_name"   # must not raise
    assert agent.name == "new_name"
