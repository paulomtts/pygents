<!-- task-pipeline: validated -->
# Leaving `agent.run()` early leaves the agent reusable (c12afa98)

Parent story: 9e421692 "Early exit from run()" (milestone 909eb7ad). Blocked by 4554b946 "A turn that is closed or cancelled stops cleanly" (done). Spec of record: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` (decisions L1, L2, L5). Plan: `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md`, Story 1 Task 2.

Note: the exploration summary feeding this spec was truncated at 8000 characters (mid-sentence in the file:line references, around `pygents/utils.py` `safe_execution`). Nothing below depends on the missing text; the utils.py state was re-checked directly (see Prerequisite).

## Prerequisite

This branch must contain the sibling's `safe_execution` fix (`async with contextlib.aclosing(func(self, *args, **kwargs)) as agen:` in `pygents/utils.py` asyncgen wrapper, commit cba4cdb). It is present in this worktree (`pygents/utils.py:38`). It must also contain the sibling's `Turn.yielding()`/`Turn.returning()` CANCELLED handling in `pygents/turn.py`. This subtask consumes those behaviours and does not modify `pygents/turn.py` or `pygents/utils.py`.

## Scope

Only `Agent.run()` in `pygents/agent.py` (currently lines 443-499), plus tests in `tests/unit/test_agent.py`.

Changes to `run()`:

- Keep a reference to the turn's generator: `turn_gen = turn.yielding()` and iterate it, instead of the inline `async for value in turn.yielding():`.
- On `GeneratorExit` / `asyncio.CancelledError` in the per-turn body, `await turn_gen.aclose()` before the per-turn cleanup, so the turn's own generator finalizes (and records `StopReason.CANCELLED` via the sibling's turn.py logic) while the agent's turn hooks are still in place and before `turn.hooks` is restored. For the coroutine-tool path (`turn.returning()`), do not add a second close; let the cancelled `returning()` finish, since it already reports CANCELLED.
- Split the inner `finally` into independently guarded steps: (1) `turn.hooks = original_hooks`; (2) reset `_current_context_queue` (fallback to `prev_queue` on `ValueError`); (3) reset `_current_context_pool` (fallback to `prev_pool` on `ValueError`). A failure in one step must not skip the others. Today a single block runs the hook restore first and the two resets share one `try`, so a failure in one step skips the rest.
- The outer `finally` stays the final safety net and always sets `_is_running = False` and `_current_turn = None`.
- The interrupted turn is not re-queued (L2). The next `run()` starts at the queue head.
- L5 ordering is kept for normal completion: AFTER_TURN fires, then `self._current_turn = None`. Per the spec of record, `_current_turn` should be `None` before AFTER_TURN fires. The current code at lines 495-496 does the reverse, and fixing that belongs to the task that owns L5, not this card. This card must not change that ordering in either direction.
- For runs that complete normally, routing, ON_TURN_VALUE and yielded `(turn, value)` behaviour does not change.

## Observable behaviour

After the consumer leaves `run()` early (by `break` out of `async for`, by `await gen.aclose()`, or by cancelling the task driving it), right after that exit returns and with no GC or extra loop iterations needed:

- `agent._is_running is False` and `agent._current_turn is None`.
- The interrupted turn has `turn.metadata.stop_reason is StopReason.CANCELLED`.
- `turn.hooks` equals the list it had before the agent added its `turn_hooks`.
- `_current_context_queue` and `_current_context_pool` are back to the values they had before the run.
- No exception reaches the caller for a plain early exit. For a task cancel, only the expected `CancelledError` reaches the caller. Nothing reaches the event loop's exception handler.
- The agent can be reused at once: `put()` a new turn and call `run()` again.

## Error paths

- If restoring hooks or resetting a context var raises, the remaining cleanup steps still run, and `_is_running` / `_current_turn` are still cleared by the outer `finally`.
- Non-cancellation exceptions from tools (including `TurnTimeoutError`) still propagate as today. The new close logic applies only to `GeneratorExit` / `CancelledError`.

## Diagnosis to capture

The issue's `aclose()` repro left `_is_running` True for two reasons:

- The `@safe_execution` wrapper did not close the inner `run()` generator, so its cleanup was deferred to the loop's asyncgen finalizer. The sibling already fixed this.
- Inside `run()`, the inner `turn.yielding()` generator was never explicitly `aclose()`'d before the `finally` restored `turn.hooks`. This subtask fixes that part.

A dedicated test must pin this down: with `aclose()`, the state is clean right after `aclose()` returns.

## Tests

All tests go in the unit tier, in `tests/unit/test_agent.py`. The milestone spec's §5 Testing says all tests live in `tests/unit`, and the plan's Global Constraints say tests sit next to the module's existing tests. `tests/integration/` is not used.

Tests follow repo style: sync `def test_...()` wrapping `asyncio.run(_body())`, with no pytest-asyncio. Each test clears `AgentRegistry` / `ToolRegistry` / `HookRegistry` itself if it registers things by name.

1. `test_early_exit_leaves_the_agent_clean` (unit):
   - Parametrized over `kind` in {`"coro"`, `"stream"`} and `exit_by` in {`"break"`, `"aclose"`, `"cancel"`}.
   - The combinations (`coro`, `break`) and (`coro`, `aclose`) call `pytest.skip`, because coroutine tools yield only once at the end, so there is no point after the first value to exit at.
   - Asserts every item under Observable behaviour. For the loop-handler check, it records `loop.set_exception_handler` calls and expects `loop_errors == []`.
2. `test_early_exit_by_aclose_is_clean_immediately` (unit): the diagnosis regression. It covers the stream tool with `aclose()`, and asserts `_is_running is False`, `_current_turn is None` and restored `turn.hooks` right after `aclose()` returns, with no `gc.collect()` or `asyncio.sleep(0)` in between. If the parametrized test already asserts this at the same point, merge this into it and name it explicitly in its docstring rather than duplicating.
3. `test_run_again_right_after_an_early_exit` (unit): break after the first value, `put()` a new `Turn`, then call `run()` again right away. It completes normally and yields the new turn's values, and the interrupted turn is not re-run (L2).
4. `test_early_exit_cleanup_steps_are_independent` (unit): force the hook-restore step to raise, for example with a `turn.hooks` setter or a list stand-in that raises. Then assert that both context vars are still restored and that `_is_running is False` / `_current_turn is None`.
5. Existing `test_run_early_break_coroutine_does_not_raise` (test_agent.py:743) and `test_run_early_break_streaming_does_not_raise` (test_agent.py:757): replace them with the parametrized test (1), which covers the same assertions and more. If either one checks something (1) does not, such as the coroutine-with-break case the parametrization skips, keep that one test as is.
6. Existing normal-completion tests for ON_TURN_VALUE, routing and AFTER_TURN must still pass unchanged.

Verification: `uv run --extra dev pytest tests/` green and `uv run --extra dev ruff check .` passes (pytest and ruff live in the `dev` extra, per `pyproject.toml`). Run `uv run --extra dev ruff format` on the files you changed only.

## Out of scope

- `Agent.close()` or a context-manager API.
- Owner-qualified hook names or saving closures (L3).
- Registry `unregister` (L4).
- Any change to AFTER_TURN ordering (L5).
- Re-queuing the interrupted turn.
- Changes to `pygents/turn.py` or `pygents/utils.py`.
- A version bump (stays 0.6.8).
- Agent-manager follow-ups.
- New dependencies or removal of any public API.

## Commit

`fix(agent): leaving run() early closes the turn and leaves the agent reusable` on an `m1`-prefixed branch.

---

# Leaving `agent.run()` early leaves the agent reusable — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a consumer leaves `Agent.run()` early (break, `aclose()`, task cancel), the agent ends idle and clean — turn closed as CANCELLED, turn hooks and context vars restored, nothing reported to the loop — and can run again at once.

**Architecture:** Two changes inside `Agent.run()` only. (1) Keep the streaming turn's generator in `turn_gen` and `await turn_gen.aclose()` from an `except` clause before the per-turn `finally`, so the turn finishes (CANCELLED, ON_COMPLETE, `turn._is_running = False`) while the agent's turn hooks are still attached and before `turn.hooks` is reassigned. (2) Split the per-turn `finally` into three independently guarded steps (hook restore, queue-var reset, pool-var reset) that re-raise the first failure only after all three ran. The outer `finally` stays as-is.

**Tech Stack:** Python >= 3.12, asyncio, pytest (sync tests wrapping `asyncio.run`, no pytest-asyncio), ruff, uv.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-leaving-agent-run-early-c12afa98/docs/superpowers/specs/task-leaving-agent-run-early-c12afa98-design.md` (prepended above). Spec of record: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md`.

**Upstream truncation note:** both the spec author's summary (capped at 2000 of 2987 chars) and the exploration summary (capped at 8000 of 9388 chars) handed to this planning stage were truncated mid-thought. That is evidence the upstream stages over-ran their brief. This plan was written from the spec file on disk and the code on disk, not from the truncated text; nothing here depends on the missing parts.

## Global Constraints

- Only `pygents/agent.py` (`Agent.run()` plus one private module-level helper) and `tests/unit/test_agent.py` change. Do not touch `pygents/turn.py` or `pygents/utils.py`.
- Tests live in `tests/unit/test_agent.py` (unit tier; `tests/integration/` is not used).
- Test style: sync `def test_...()` that calls `asyncio.run(_body())`; no pytest-asyncio. Each test calls `AgentRegistry.clear()` (and `HookRegistry.clear()` when it adds hooks) at the top; hook function names must be unique within the module.
- The interrupted turn is not re-queued (L2). The next `run()` starts at the queue head.
- Do not change L5 ordering: after a normally completed turn, AFTER_TURN fires and then `self._current_turn = None` (agent.py:495-496). Leave those two lines exactly as they are.
- Normal-completion behaviour (routing, ON_TURN_VALUE, yielded `(turn, value)`, AFTER_TURN) is unchanged.
- No `Agent.close()` / context-manager API, no L3/L4 work, no version bump (stays 0.6.8), no new dependencies, no public API removed.
- Verification: `uv run --extra dev pytest tests/`, `uv run --extra dev ruff check .`, and `uv run --extra dev ruff format pygents/agent.py tests/unit/test_agent.py` (pytest and ruff live in the `dev` extra). `pygents/agent.py` already has pre-existing lines that `ruff format` would reflow (verified: `ruff format --check pygents/agent.py` fails on this branch before any of this card's edits, e.g. the `_run_hooks` call around line 149). `ruff format` has no partial-file mode, so after running it, `git diff pygents/agent.py` and keep only the hunks inside `Agent.run()` / the new `_reset_context_var` helper; revert any hunk outside that scope (e.g. with `git checkout -p pygents/agent.py`) before committing, so the diff stays scoped to this card's change.
- Commit message for the headline fix: `fix(agent): leaving run() early closes the turn and leaves the agent reusable`.

## Decisions this plan makes where the spec is silent or cannot be met literally

Read these before Task 1; a reviewer should check each.

1. **A bare `break` cannot be cleaned up "immediately".** Breaking out of `async for x in agent.run()` does not tell the generator anything: CPython drops the generator and asyncio's asyncgen finalizer schedules `aclose()` in a separate task on a later loop iteration. No code inside `run()` can change that, and because that task runs in a copied `Context`, the consumer task's `_current_context_queue` / `_current_context_pool` stay set to the agent's objects until the consumer task ends. So: the parametrized test's `"break"` case uses the Python-documented deterministic form, `async with contextlib.aclosing(agent.run()) as run_gen: async for ...: break`, and asserts every observable immediately. The bare-`break` path gets its own test (`test_early_exit_by_plain_break_is_cleaned_up_by_the_loop`) that waits a bounded number of loop ticks for the finalizer, then asserts agent state, turn state, hooks and `loop_errors == []` — but not the consumer's context vars, which are unfixable from inside `run()`.
2. **The close runs on any exception, not only `GeneratorExit` / `CancelledError`.** The spec limits the close to those two, reasoning that tool exceptions must propagate as today. When a tool raises (including `TurnTimeoutError`), `turn_gen` has already finished, so `aclose()` is a no-op and that behaviour is unchanged. But when the agent's *own* code raises while `turn_gen` is paused at a yield — an ON_TURN_VALUE hook raising, or `_route_value` → `put()` rejecting a streamed `Turn` — restoring `turn.hooks` raises `SafeExecutionError` ("Cannot change property 'hooks' while the turn is running"), which hides the real error. So the clause is `except BaseException:` with a no-op-if-finished close, and Review Focus item 3 pins it with a test.
3. **Cleanup failures still propagate.** After all three cleanup steps have run, the first failure among them is re-raised, so a broken hook-restore is not swallowed. The spec only requires that the other steps still run.
4. **Test 2 is merged into test 1.** The parametrized test asserts state right after `aclose()` returns, with no `gc.collect()` or `asyncio.sleep(0)` in between. Per the spec, its docstring names the diagnosis instead of a duplicate test.
5. **Existing break tests:** `test_run_early_break_coroutine_does_not_raise` (test_agent.py:743) stays, because it covers coroutine + bare break, which the parametrization skips. `test_run_early_break_streaming_does_not_raise` (test_agent.py:757) is replaced by `test_early_exit_by_plain_break_is_cleaned_up_by_the_loop`, which asserts everything the old test did and more.

## Review Focus

1. Bare `break` without `aclosing` (the most common consumer code). Expect: once the loop has run the asyncgen finalizer, the agent is idle, the turn is CANCELLED with its hooks restored, and nothing reaches the loop's exception handler (the context-var restore for the consumer's own `Context` is impossible — see Decision 1). Test: `test_early_exit_by_plain_break_is_cleaned_up_by_the_loop` (Task 1).
2. A task cancel that lands while `run()` is parked in an agent hook with `turn.yielding()` paused at a yield, not inside the tool. Expect: the same clean state as a cancel inside the tool, and only `CancelledError` reaches the caller. Test: the `("stream", "cancel")` case of `test_early_exit_leaves_the_agent_clean`, which parks `run()` in a blocking ON_TURN_VALUE hook (Task 1).
3. The agent's own ON_TURN_VALUE hook raises while a streaming turn is paused. Expect: the hook's own exception reaches the caller (not `SafeExecutionError`), the turn is closed as CANCELLED, and hooks and context vars are restored. Test: `test_stream_on_turn_value_error_propagates_and_closes_the_turn` (Task 1).
4. Re-running at once with other turns still queued. Expect: the next `run()` starts at the queue head, the interrupted turn never runs again, and later `put()` turns follow. Test: `test_run_again_right_after_an_early_exit` (Task 1).
5. Cleanup running in a different `Context` (the finalizer task on the bare-break path), where `ContextVar.reset(token)` raises `ValueError`. Expect: the fallback `set(previous)` absorbs it and nothing is reported to the loop. Test: `loop_errors == []` in `test_early_exit_by_plain_break_is_cleaned_up_by_the_loop` (Task 1). The helper that keeps this fallback per variable is added in Task 2, and the Task 1 test keeps guarding it.

---

### Task 1: Close the streaming turn's generator when `run()` is left early

**Files:**
- Modify: `pygents/agent.py:468-486` (per-turn body inside `Agent.run()`)
- Test: `tests/unit/test_agent.py:1-59` (decision-table docstring), `:61-77` (imports), `:757-767` (replace `test_run_early_break_streaming_does_not_raise`)

**Interfaces:**
- Consumes: `Turn.yielding()` (async generator, wrapped by `safe_execution` with `contextlib.aclosing`; on `GeneratorExit`/`CancelledError` it cancels its producer, sets `StopReason.CANCELLED`, fires ON_COMPLETE, sets `turn._is_running = False`). `Turn.returning()` (on `CancelledError` sets CANCELLED and re-raises). Module-level test tools `stream_agent` (yields 1, 2, 3) and `slow_tool_agent(duration: float)`, both already in `tests/unit/test_agent.py`.
- Produces: in `Agent.run()`, a local `turn_gen` (the `turn.yielding()` generator, or `None` on the coroutine path) and an `except BaseException:` clause that runs `await turn_gen.aclose()` before the per-turn `finally`. Tests: `test_early_exit_leaves_the_agent_clean(kind, exit_by)`, `test_early_exit_by_plain_break_is_cleaned_up_by_the_loop`, `test_stream_on_turn_value_error_propagates_and_closes_the_turn`, `test_run_again_right_after_an_early_exit`. Test-module imports `contextlib`, `gc`, `_current_context_queue`, `_current_context_pool` (Task 2 reuses them).

- [ ] **Step 1: Update the decision-table docstring and imports in `tests/unit/test_agent.py`**

In the module docstring, replace these two lines:

```
  R9  Early break (coroutine tool) -> no ValueError; _is_running False
  R10 Early break (streaming tool) -> no ValueError; _is_running False
```

with:

```
  R9  Early break (coroutine tool, bare break) -> no ValueError; _is_running False
  R10 Early exit (stream: break inside aclosing / aclose() / task cancel; coro: task cancel)
      -> turn generator closed before cleanup; right after the exit returns: agent idle,
         turn CANCELLED (ON_COMPLETE sees it with the agent's turn hooks attached),
         turn.hooks and both context vars restored, nothing reaches the loop's exception handler
  R11 Bare break (no aclosing) -> cleaned up once the loop runs its asyncgen finalizer;
      agent idle, turn CANCELLED, turn.hooks restored, no loop errors
  R12 Agent's ON_TURN_VALUE hook raises while a streaming turn is paused -> the hook's
      error propagates (not SafeExecutionError); turn closed as CANCELLED; state restored
  R13 run() again right after an early exit -> works at once; interrupted turn not re-queued
```

Replace the import block at lines 61-77:

```python
import asyncio
from typing import cast

import pytest

from pygents.agent import Agent
from pygents.context import ContextQueue
```

with:

```python
import asyncio
import contextlib
import gc
from typing import cast

import pytest

from pygents.agent import Agent
from pygents.context import (
    ContextQueue,
    _current_context_pool,
    _current_context_queue,
)
```

(Lines from `from pygents.errors import (` down to `from pygents.turn import StopReason, Turn` stay as they are.)

- [ ] **Step 2: Write the failing tests**

Replace `test_run_early_break_streaming_does_not_raise` (tests/unit/test_agent.py:757-767, the whole function) with the following four tests. Leave `test_run_early_break_coroutine_does_not_raise` (lines 743-754) untouched.

```python
@pytest.mark.parametrize("exit_by", ["break", "aclose", "cancel"])
@pytest.mark.parametrize("kind", ["coro", "stream"])
def test_early_exit_leaves_the_agent_clean(kind, exit_by):
    """R10. Leaving run() early leaves the agent idle, the turn CANCELLED and
    all per-turn state restored, checked right after the exit returns (no
    gc.collect() and no asyncio.sleep(0) in between).

    The ("stream", "aclose") case is the diagnosis regression for the issue's
    aclose() repro: run()'s inner turn.yielding() generator was left paused at
    a yield, so turn._is_running stayed True, restoring turn.hooks raised
    SafeExecutionError and the context-var resets were skipped.

    "break" means break inside contextlib.aclosing(agent.run()), the form that
    closes the generator in the consumer's own task. A bare break is covered by
    test_early_exit_by_plain_break_is_cleaned_up_by_the_loop.
    """
    if kind == "coro" and exit_by in ("break", "aclose"):
        pytest.skip(
            "coroutine tools yield once, at the end: "
            "there is no point after the first value to exit at"
        )
    AgentRegistry.clear()
    HookRegistry.clear()
    if kind == "stream":
        agent = Agent("a", "desc", [stream_agent])
        turn = Turn("stream_agent")
    else:
        agent = Agent("a", "desc", [slow_tool_agent])
        turn = Turn("slow_tool_agent", kwargs={"duration": 3600}, timeout=7200)
    completed = []
    started = asyncio.Event()

    @turn.on_complete
    async def early_exit_turn_complete(t, stop_reason):
        completed.append(("turn", stop_reason))

    @agent.on_complete
    async def early_exit_agent_complete(t, stop_reason):
        completed.append(("agent", stop_reason))

    if exit_by == "cancel" and kind == "stream":
        # Park run() inside its own hook while turn.yielding() is paused at a
        # yield, so the cancel lands in run()'s body, not inside the tool.
        @agent.on_turn_value
        async def early_exit_park_on_value(a, t, value):
            started.set()
            await asyncio.sleep(3600)

    if exit_by == "cancel" and kind == "coro":

        @turn.before_run
        async def early_exit_tool_started(t):
            started.set()

    hooks_before = list(turn.hooks)
    loop_errors = []
    observed = {}

    def snapshot():
        observed["is_running"] = agent._is_running
        observed["current_turn"] = agent._current_turn
        observed["queue"] = _current_context_queue.get()
        observed["pool"] = _current_context_pool.get()
        observed["hooks"] = list(turn.hooks)
        observed["stop_reason"] = turn.metadata.stop_reason

    async def exit_early():
        observed["queue_before"] = _current_context_queue.get()
        observed["pool_before"] = _current_context_pool.get()
        if exit_by == "break":
            async with contextlib.aclosing(agent.run()) as run_gen:
                async for _ in run_gen:
                    break
            snapshot()
        elif exit_by == "aclose":
            run_gen = agent.run()
            assert await run_gen.__anext__() == (turn, 1)
            await run_gen.aclose()
            snapshot()
        else:
            try:
                async for _ in agent.run():
                    pass
            except asyncio.CancelledError:
                snapshot()
                raise

    async def _body():
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: loop_errors.append(context)
        )
        await agent.put(turn)
        if exit_by == "cancel":
            task = asyncio.create_task(exit_early())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await exit_early()

    asyncio.run(_body())
    gc.collect()  # flush any "Task exception was never retrieved" reports

    assert observed["is_running"] is False
    assert observed["current_turn"] is None
    assert observed["queue"] is observed["queue_before"]
    assert observed["pool"] is observed["pool_before"]
    assert observed["hooks"] == hooks_before
    assert observed["stop_reason"] is StopReason.CANCELLED
    assert sorted(completed) == [
        ("agent", StopReason.CANCELLED),
        ("turn", StopReason.CANCELLED),
    ]
    assert loop_errors == []


def test_early_exit_by_plain_break_is_cleaned_up_by_the_loop():
    """R11. A bare break (no aclosing) only drops the generator; asyncio's
    asyncgen finalizer closes it in a separate task on a later loop iteration.
    Once that has run, the agent is idle, the turn is CANCELLED with its hooks
    restored, and nothing reached the loop's exception handler.

    The consumer task's own context vars are not asserted: the finalizer runs
    in a copied Context, so run() cannot reset them for a bare break.
    Replaces test_run_early_break_streaming_does_not_raise.
    """
    AgentRegistry.clear()
    HookRegistry.clear()
    agent = Agent("a", "desc", [stream_agent])
    turn = Turn("stream_agent")
    completed = []
    loop_errors = []

    @agent.on_complete
    async def plain_break_agent_complete(t, stop_reason):
        completed.append(stop_reason)

    async def _body():
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: loop_errors.append(context)
        )
        await agent.put(turn)
        async for _ in agent.run():
            break
        # Bounded wait for the finalizer task; _is_running goes False last.
        for _ in range(1000):
            if not agent._is_running:
                break
            await asyncio.sleep(0)

    asyncio.run(_body())
    gc.collect()  # flush any "Task exception was never retrieved" reports

    assert agent._is_running is False
    assert agent._current_turn is None
    assert turn.hooks == []
    assert turn.metadata.stop_reason is StopReason.CANCELLED
    assert completed == [StopReason.CANCELLED]
    assert loop_errors == []


def test_stream_on_turn_value_error_propagates_and_closes_the_turn():
    """R12. An error from the agent's own ON_TURN_VALUE hook, raised while the
    streaming turn is paused at a yield, reaches the caller as itself (not as
    SafeExecutionError from restoring turn.hooks on a still-running turn)."""
    AgentRegistry.clear()
    HookRegistry.clear()
    agent = Agent("a", "desc", [stream_agent])
    turn = Turn("stream_agent")
    completed = []
    observed = {}

    @agent.on_complete
    async def value_error_agent_complete(t, stop_reason):
        completed.append(stop_reason)

    @agent.on_turn_value
    async def value_error_exploding_on_turn_value(a, t, value):
        raise RuntimeError("value hook failed")

    async def _body():
        queue_before = _current_context_queue.get()
        pool_before = _current_context_pool.get()
        await agent.put(turn)
        with pytest.raises(RuntimeError, match="value hook failed"):
            async for _ in agent.run():
                pass
        observed["queue_restored"] = _current_context_queue.get() is queue_before
        observed["pool_restored"] = _current_context_pool.get() is pool_before

    asyncio.run(_body())

    assert observed == {"queue_restored": True, "pool_restored": True}
    assert agent._is_running is False
    assert agent._current_turn is None
    assert turn.hooks == []
    assert turn.metadata.stop_reason is StopReason.CANCELLED
    assert completed == [StopReason.CANCELLED]


def test_run_again_right_after_an_early_exit():
    """R13. After breaking out after the first value, run() works again at
    once (no GC, no loop ticks); it starts at the queue head and the
    interrupted turn is not re-run (L2)."""
    AgentRegistry.clear()
    agent = Agent("a", "desc", [stream_agent])
    interrupted = Turn("stream_agent")
    queued = Turn("stream_agent")
    added_after = Turn("stream_agent")

    async def _body():
        await agent.put(interrupted)
        await agent.put(queued)
        async with contextlib.aclosing(agent.run()) as run_gen:
            async for first in run_gen:
                break
        assert first == (interrupted, 1)
        await agent.put(added_after)
        return [item async for item in agent.run()]

    items = asyncio.run(_body())

    assert items == [
        (queued, 1),
        (queued, 2),
        (queued, 3),
        (added_after, 1),
        (added_after, 2),
        (added_after, 3),
    ]
    assert interrupted.metadata.stop_reason is StopReason.CANCELLED
    assert queued.metadata.stop_reason is StopReason.COMPLETED
    assert added_after.metadata.stop_reason is StopReason.COMPLETED
    assert agent._is_running is False
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run --extra dev pytest tests/unit/test_agent.py -v -k "early_exit or plain_break or on_turn_value_error_propagates or run_again_right_after"`

Expected:
- `test_early_exit_leaves_the_agent_clean[coro-break]` and `[coro-aclose]`: SKIPPED.
- `test_early_exit_leaves_the_agent_clean[coro-cancel]`: PASS already (the coroutine path was never broken; it stays as a guard).
- `[stream-break]`, `[stream-aclose]`: FAIL with `SafeExecutionError: Cannot change property 'hooks' while the turn is running.` raised out of `aclosing` / `aclose()`.
- `[stream-cancel]`: FAIL — `await task` raises `SafeExecutionError` instead of `CancelledError`.
- `test_early_exit_by_plain_break_is_cleaned_up_by_the_loop`: FAIL at `assert turn.hooks == []` (the agent's on_complete hook is still attached).
- `test_stream_on_turn_value_error_propagates_and_closes_the_turn`: FAIL — `SafeExecutionError` raised instead of `RuntimeError`.
- `test_run_again_right_after_an_early_exit`: FAIL with `SafeExecutionError` out of `aclosing`.

If any `[stream-*]` case passes here, stop and report: the sibling's turn.py/utils.py behaviour is not what this plan assumes.

- [ ] **Step 4: Implement the close in `Agent.run()`**

In `pygents/agent.py`, replace lines 468-486:

```python
                original_hooks = turn.hooks[:]
                turn.hooks.extend(self.turn_hooks)
                try:
                    if inspect.isasyncgenfunction(turn.tool.fn):
                        async for value in turn.yielding():
                            await self._route_value(value)
                            await self._run_hooks(
                                AgentHook.ON_TURN_VALUE, self, turn, value
                            )
                            if not isinstance(value, (ContextItem, Turn)):
                                yield (turn, value)
                    else:
                        output = await turn.returning()
                        await self._route_value(turn.output)
                        await self._run_hooks(
                            AgentHook.ON_TURN_VALUE, self, turn, output
                        )
                        if not isinstance(output, (ContextItem, Turn)):
                            yield (turn, output)
```

with:

```python
                original_hooks = turn.hooks[:]
                turn.hooks.extend(self.turn_hooks)
                turn_gen = None
                try:
                    if inspect.isasyncgenfunction(turn.tool.fn):
                        turn_gen = turn.yielding()
                        async for value in turn_gen:
                            await self._route_value(value)
                            await self._run_hooks(
                                AgentHook.ON_TURN_VALUE, self, turn, value
                            )
                            if not isinstance(value, (ContextItem, Turn)):
                                yield (turn, value)
                    else:
                        output = await turn.returning()
                        await self._route_value(turn.output)
                        await self._run_hooks(
                            AgentHook.ON_TURN_VALUE, self, turn, output
                        )
                        if not isinstance(output, (ContextItem, Turn)):
                            yield (turn, output)
                except BaseException:
                    # Leaving early (aclose()/break -> GeneratorExit, task cancel
                    # -> CancelledError) or an error from our own routing/hooks can
                    # leave turn_gen paused at a yield with turn._is_running True.
                    # Close it now, while the agent's turn hooks are still attached,
                    # so the turn records CANCELLED and fires ON_COMPLETE before the
                    # cleanup below reassigns turn.hooks. No-op if turn_gen already
                    # finished (e.g. the tool raised). The coroutine path needs no
                    # close: a cancelled returning() already reports CANCELLED.
                    if turn_gen is not None:
                        await turn_gen.aclose()
                    raise
```

Leave the `finally:` block at lines 487-494, the AFTER_TURN / `_current_turn = None` lines 495-496, and the outer `finally` at lines 497-499 exactly as they are.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run --extra dev pytest tests/unit/test_agent.py -v -k "early_exit or plain_break or on_turn_value_error_propagates or run_again_right_after"`

Expected: all PASS except `[coro-break]` and `[coro-aclose]`, which are SKIPPED.

- [ ] **Step 6: Run the full suite to confirm normal-completion behaviour is unchanged**

Run: `uv run --extra dev pytest tests/`

Expected: all PASS (the only skips are the two `[coro-break]` / `[coro-aclose]` cases plus any skips that existed before). In particular `test_run_before_turn_after_turn_and_on_turn_value_hooks_called`, `test_run_on_turn_value_hook_raises_propagates_and_cleans_up`, `test_run_early_break_coroutine_does_not_raise`, `test_run_propagates_turn_timeout_error` and the routing tests pass unchanged.

- [ ] **Step 7: Lint and format the touched files**

Run: `uv run --extra dev ruff format pygents/agent.py tests/unit/test_agent.py && uv run --extra dev ruff check .`

`pygents/agent.py` already has pre-existing lines outside `Agent.run()` that `ruff format` reflows on this branch (e.g. the `_run_hooks` call around line 149), unrelated to this card. After running the command, run `git diff pygents/agent.py` and revert any hunk that falls outside `Agent.run()` and the new `_reset_context_var` helper (for example `git checkout -p pygents/agent.py` and skip that hunk), so the diff stays scoped to this card's change.

Expected: ruff check reports `All checks passed!`. If `ruff format` reflows lines inside the scoped region, re-run Step 6 before committing.

- [ ] **Step 8: Commit**

```bash
git add pygents/agent.py tests/unit/test_agent.py
git commit -m "fix(agent): leaving run() early closes the turn and leaves the agent reusable" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NBto4a38sXMwEQEvaWTB5w"
```

---

### Task 2: Make the per-turn cleanup steps independent

**Files:**
- Modify: `pygents/agent.py:1-5` (imports), `:27-31` (add helper after `_tool_registry_keys`), the per-turn `finally:` block inside `Agent.run()` (lines 487-494 before Task 1; after Task 1's edit it sits directly below the new `except BaseException:` clause)
- Test: `tests/unit/test_agent.py` (decision-table docstring; new test class and test after `test_run_again_right_after_an_early_exit`)

**Interfaces:**
- Consumes: Task 1's `except BaseException:` close (so `turn._is_running` is already False when the cleanup runs). Test-module imports `_current_context_queue`, `_current_context_pool` from Task 1 Step 1; module-level tool `stream_agent`.
- Produces: `pygents.agent._reset_context_var(var: ContextVar[Any], token: Token[Any], previous: Any) -> None` (private; resets with `token`, falling back to `var.set(previous)` on `ValueError`). A per-turn `finally` that runs hook restore, queue-var reset and pool-var reset each in its own `try`, then re-raises the first failure. Test helper class `_HookRestoreFailsTurn(Turn)` and test `test_early_exit_cleanup_steps_are_independent`.

- [ ] **Step 1: Add R14 to the decision-table docstring**

In the `tests/unit/test_agent.py` module docstring, directly after the R13 line added in Task 1, add:

```
  R14 Hook-restore step raises -> both context vars still reset; _is_running False,
      _current_turn None; the restore failure propagates to the caller
```

- [ ] **Step 2: Write the failing test**

Add after `test_run_again_right_after_an_early_exit` in `tests/unit/test_agent.py`:

```python
class _HookRestoreFailsTurn(Turn):
    """A Turn whose `hooks` attribute rejects reassignment once armed, used to
    force run()'s hook-restore cleanup step to fail."""

    def __setattr__(self, name, value):
        if name == "hooks" and getattr(self, "_reject_hooks", False):
            raise RuntimeError("hook restore failed")
        super().__setattr__(name, value)


def test_early_exit_cleanup_steps_are_independent():
    """R14. When restoring turn.hooks fails during an early exit, the two
    context-var resets still run and the agent still ends idle."""
    AgentRegistry.clear()
    agent = Agent("a", "desc", [stream_agent])
    turn = _HookRestoreFailsTurn("stream_agent")
    turn._reject_hooks = True
    observed = {}

    async def _body():
        queue_before = _current_context_queue.get()
        pool_before = _current_context_pool.get()
        await agent.put(turn)
        run_gen = agent.run()
        assert await run_gen.__anext__() == (turn, 1)
        with pytest.raises(RuntimeError, match="hook restore failed"):
            await run_gen.aclose()
        observed["queue_restored"] = _current_context_queue.get() is queue_before
        observed["pool_restored"] = _current_context_pool.get() is pool_before

    asyncio.run(_body())

    assert observed == {"queue_restored": True, "pool_restored": True}
    assert agent._is_running is False
    assert agent._current_turn is None
    assert turn.metadata.stop_reason is StopReason.CANCELLED
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run --extra dev pytest tests/unit/test_agent.py::test_early_exit_cleanup_steps_are_independent -v`

Expected: FAIL at `assert observed == {"queue_restored": True, "pool_restored": True}` with both values `False` — the hook restore raises before the context-var resets run, so the consumer's context still holds the agent's queue and pool.

- [ ] **Step 4: Add the `_reset_context_var` helper**

In `pygents/agent.py`, replace the first import lines (1-5):

```python
from __future__ import annotations

import asyncio
import inspect
from typing import Any, AsyncIterator, Sequence
```

with:

```python
from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar, Token
from typing import Any, AsyncIterator, Sequence
```

Then, directly after `_tool_registry_keys` (ends at line 31), add:

```python
def _reset_context_var(var: ContextVar[Any], token: Token[Any], previous: Any) -> None:
    """Reset *var* with *token*; if the token was made in another Context
    (cleanup running in the loop's asyncgen-finalizer task), set *previous*."""
    try:
        var.reset(token)
    except ValueError:
        var.set(previous)
```

- [ ] **Step 5: Split the per-turn `finally` into guarded steps**

In `Agent.run()`, replace the per-turn `finally:` block (directly below Task 1's `except BaseException:` clause):

```python
                finally:
                    turn.hooks = original_hooks
                    try:
                        _current_context_queue.reset(queue_token)
                        _current_context_pool.reset(pool_token)
                    except ValueError:
                        _current_context_queue.set(prev_queue)
                        _current_context_pool.set(prev_pool)
```

with:

```python
                finally:
                    # Three independent steps: a failure in one must not skip the
                    # others. The first failure is re-raised once all have run.
                    cleanup_error: Exception | None = None
                    try:
                        turn.hooks = original_hooks
                    except Exception as exc:
                        cleanup_error = exc
                    try:
                        _reset_context_var(
                            _current_context_queue, queue_token, prev_queue
                        )
                    except Exception as exc:
                        cleanup_error = cleanup_error or exc
                    try:
                        _reset_context_var(
                            _current_context_pool, pool_token, prev_pool
                        )
                    except Exception as exc:
                        cleanup_error = cleanup_error or exc
                    if cleanup_error is not None:
                        raise cleanup_error
```

Do not touch the two lines after this block (`await self._run_hooks(AgentHook.AFTER_TURN, self, turn)` then `self._current_turn = None`) or the outer `finally`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run --extra dev pytest tests/unit/test_agent.py::test_early_exit_cleanup_steps_are_independent -v`

Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run --extra dev pytest tests/`

Expected: all PASS (the only skips are `test_early_exit_leaves_the_agent_clean[coro-break]` / `[coro-aclose]` plus any skips that existed before).

- [ ] **Step 8: Lint and format the touched files**

Run: `uv run --extra dev ruff format pygents/agent.py tests/unit/test_agent.py && uv run --extra dev ruff check .`

`pygents/agent.py` already has pre-existing lines outside `Agent.run()` that `ruff format` reflows on this branch (e.g. the `_run_hooks` call around line 149), unrelated to this card. After running the command, run `git diff pygents/agent.py` and revert any hunk that falls outside `Agent.run()` and the `_reset_context_var` helper (for example `git checkout -p pygents/agent.py` and skip that hunk), so the diff stays scoped to this card's change.

Expected: ruff check reports `All checks passed!`. If `ruff format` reflows lines inside the scoped region, re-run Step 7 before committing.

- [ ] **Step 9: Commit**

```bash
git add pygents/agent.py tests/unit/test_agent.py
git commit -m "fix(agent): run() per-turn cleanup steps no longer skip each other" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NBto4a38sXMwEQEvaWTB5w"
```

---

## Spec coverage map

| Spec item | Where |
|---|---|
| `turn_gen = turn.yielding()` kept and closed on early exit, before hook restore; coroutine path not double-closed | Task 1 Step 4 |
| Guarded cleanup steps (hooks / queue var / pool var), one failure doesn't skip others | Task 2 Steps 4-5 |
| Outer `finally` unchanged as the final safety net | Task 1 Step 4, Task 2 Step 5 ("do not touch") |
| No re-queue of the interrupted turn (L2) | Task 1 `test_run_again_right_after_an_early_exit` |
| L5 ordering left exactly as is | Global Constraints; Task 1 Step 4, Task 2 Step 5 |
| Normal completion unchanged | Task 1 Step 6, Task 2 Step 7 (full suite, existing tests untouched) |
| Observable behaviour (idle, CANCELLED, hooks, context vars, no loop errors, reusable) | Task 1 `test_early_exit_leaves_the_agent_clean`, `test_run_again_right_after_an_early_exit` |
| Error path: cleanup failure still runs other steps and clears `_is_running`/`_current_turn` | Task 2 `test_early_exit_cleanup_steps_are_independent` |
| Error path: tool exceptions/`TurnTimeoutError` propagate as today | Task 1 Step 6 (existing `test_run_propagates_turn_timeout_error`, `test_run_on_turn_error_hook_called`); Decision 2 |
| Diagnosis test (aclose clean immediately) | merged into `[stream-aclose]` case, named in its docstring (Decision 4) |
| Spec test 5 (existing break tests) | Decision 5; Task 1 Step 2 |
| Tests in `tests/unit/test_agent.py`, sync `asyncio.run` style, registries cleared | every test in Tasks 1-2 |
| Commit message | Task 1 Step 8 |
