<!-- task-pipeline: validated -->
# A turn that is closed or cancelled stops cleanly (card 4554b946)

Parent story: 9e421692 "Early exit from run()" (milestone 909eb7ad). Source of truth: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` decision L1 (the Turn half only), and `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md` Task 1.1. This document narrows that agreed design to this subtask; it adds no new decisions.

## Scope

Change `pygents/turn.py` only; add tests to `tests/unit/test_turn.py` only. No signature changes, no new public API, no new dependencies, version stays 0.6.8.

1. `Turn.yielding()` (turn.py:279-355). Today, if the consumer stops early (`aclose()`, `break` followed by close, or the consuming task is cancelled), `GeneratorExit`/`asyncio.CancelledError` is not caught by `except Exception`, so the `producer` task created at line 307 is left running and `stop_reason` stays unset. Add an `except (GeneratorExit, asyncio.CancelledError):` handler to the inner `try` that wraps the queue-consume loop (lines 310-329, ending at `await producer`). The handler must:
   - call `producer.cancel()`;
   - `await producer`, swallowing `(asyncio.CancelledError, Exception)`;
   - set `self.metadata.stop_reason = StopReason.CANCELLED`;
   - re-raise the original exception.
   It must not `yield`. Awaiting inside it is allowed only because `aclose()` drives the generator. The existing timeout handler (lines 330-342) and the outer `finally` (lines 352-355, which sets `end_time`, fires `ON_COMPLETE`, and clears `_is_running`) stay as they are.
2. `Turn.returning()` (turn.py:240-276). Add `except asyncio.CancelledError:` that sets `self.metadata.stop_reason = StopReason.CANCELLED` and re-raises. Put it before the generic `except Exception as e:` (lines 269-272) so cancellation never goes down the ERROR / `ON_ERROR` path. The existing `finally` is unchanged.

`StopReason.CANCELLED` already exists (turn.py:29-33) but nothing uses it yet. This change makes it the reported reason in both paths.

## Observable behaviour

- Closing a `yielding()` generator early, or cancelling the task that consumes it, results in: the producer task is done, and the tool's async generator is closed rather than left running; `metadata.stop_reason == StopReason.CANCELLED`; `metadata.end_time` is set; `ON_COMPLETE` fires once with `StopReason.CANCELLED`; `ON_ERROR` and `ON_TIMEOUT` do not fire; `_is_running` is `False`, so the same Turn can be started again without `SafeExecutionError`.
- Cancelling a task that is awaiting `returning()` results in: `asyncio.CancelledError` reaches the caller; `stop_reason == StopReason.CANCELLED`; `ON_COMPLETE` fires with `CANCELLED`; `ON_ERROR` does not fire; `_is_running` is `False`.
- Completion, timeout (`TurnTimeoutError`, `TIMEOUT`), and tool-error (`ERROR`, `ON_ERROR`) behaviour in both methods stays exactly as it is now.

## Error paths

- The exception that triggered cancellation (`GeneratorExit` or `CancelledError`) is always re-raised and never swallowed or replaced. The only thing swallowed is the producer's own exception from its cancellation await.
- If the producer is already finished or failed when the consumer closes, `await producer` must not raise out of the handler. That is why it swallows `Exception`.

## Known risk for the plan stage

`yielding()` is wrapped by `safe_execution` (pygents/utils.py:26-37), which is itself an async generator that re-yields from the inner generator. Calling `aclose()` on the object the caller holds closes the wrapper. The inner `yielding()` generator may then be finalized through the event loop's asyncgen finalizer instead of synchronously. If so, the observable state above would only hold after one or more event-loop iterations, not right after `aclose()` returns. The plan must check this. utils.py is outside this card's file scope, so if it needs changing, raise that with the plan stage instead of doing it silently here. Tests must not hide the gap with arbitrary sleeps unless the plan decides that is the intended contract.

## Out of scope

Everything in `pygents/agent.py`, including `Agent.run()` closing `turn_gen`, hook and context-var restoration, and `_is_running`/`_current_turn` cleanup. All of that belongs to sibling card c12afa98, which is blocked on this card. Also out of scope: re-queuing the interrupted turn (L2), Agent.close() or a context-manager API, owner-qualified hook names, and the version bump or release.

## Tests

Tier: all tests go in `tests/unit/test_turn.py`. They are unit tests. The rule comes from the milestone design spec §5 ("Everything in tests/unit (the existing layout)") and the card rule "Tests live in tests/unit/ next to the module's existing tests". These tests cover Turn-only behaviour, not cross-module wiring, so none belong in `tests/integration/`.

Conventions: follow the file's decision-table style. Each test is a sync `def test_...(): asyncio.run(_body())`, since there is no pytest-asyncio. Tools use unique names, and each test clears `ToolRegistry` (and `HookRegistry` where hooks are registered), following the existing tests such as `test_returning_async_when_tool_raises_sets_stop_reason_error_and_propagates` and `test_yielding_async_when_tool_raises_sets_stop_reason_error_and_propagates`.

1. (unit) `test_yielding_when_consumer_closes_early_sets_stop_reason_cancelled`: take the first item from a multi-item async-gen tool, `aclose()` the generator, then check `stop_reason == CANCELLED`, that `end_time` is set, and that `_is_running is False`.
2. (unit) `test_yielding_when_consumer_closes_early_cancels_producer`: the tool generator's `finally`/cleanup ran, meaning it was closed rather than left running, and no pending tasks from the turn remain.
3. (unit) `test_yielding_when_consumer_closes_early_fires_on_complete_with_cancelled_not_on_error`: `ON_COMPLETE` fires once with `CANCELLED`; `ON_ERROR` and `ON_TIMEOUT` do not fire.
4. (unit) `test_yielding_when_consuming_task_cancelled_sets_stop_reason_cancelled_and_propagates`: cancel a task that is blocked waiting for the next item from a slow tool. The awaiter gets `CancelledError`, `stop_reason == CANCELLED`, and `_is_running is False`.
5. (unit) `test_yielding_after_cancel_turn_can_run_again`: after an early close, running the same Turn again does not raise `SafeExecutionError`.
6. (unit) `test_returning_when_task_cancelled_sets_stop_reason_cancelled_and_propagates`: cancel a task awaiting `returning()` on a slow coroutine tool. The caller gets `CancelledError`, `stop_reason == CANCELLED`, and `_is_running is False`.
7. (unit) `test_returning_when_task_cancelled_fires_on_complete_with_cancelled_not_on_error`: `ON_COMPLETE` fires with `CANCELLED`; `ON_ERROR` does not fire.
8. (unit) `test_yielding_when_consumer_closes_early_waits_for_slow_tool_cleanup`: the milestone plan's Review Focus item 2 — a tool whose `finally` awaits (e.g. `await asyncio.sleep(0.02)`) before recording that it cleaned up. `aclose()` must not return until that cleanup finishes: after `aclose()` returns, assert the cleanup marker was recorded, `stop_reason == CANCELLED`, and no pending tasks from the turn remain. This exercises `await producer` in the new handler actually waiting out a slow producer rather than just cancelling and moving on.

Existing completion, timeout and error tests in `tests/unit/test_turn.py` must keep passing unchanged, which serves as the regression check.

## Verification

- Full suite: `uv run pytest tests/`
- Typecheck: none
- Lint: `uv run ruff format .` and `uv run ruff check .`
- Source: `.github/workflows/tests.yml`, `.github/workflows/ruff.yml`

---

# A Turn That Is Closed or Cancelled Stops Cleanly — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Turn.returning()` and `Turn.yielding()` report `StopReason.CANCELLED`, stop their producer/tool, and leave the Turn reusable the moment the caller closes or cancels them.

**Architecture:** `returning()` gains an `except asyncio.CancelledError` branch ahead of the generic `except Exception`. `yielding()` gains an `except (GeneratorExit, asyncio.CancelledError)` branch around its queue-consume loop that cancels and awaits the producer task. Because `safe_execution` wraps `yielding()` in a second async generator that does not close the inner one, `aclose()` on the caller's object would otherwise leave all of this to the event loop's asyncgen finalizer on a later loop iteration; the wrapper is changed to close the inner generator itself with `contextlib.aclosing` (see "Scope decision" below).

**Tech Stack:** Python >= 3.12, asyncio, pytest (no pytest-asyncio), ruff, uv.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-a-turn-that-is-closed-4554b946/docs/superpowers/specs/task-a-turn-that-is-closed-4554b946-design.md` (reproduced verbatim above). Upstream: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` L1, `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md` Task 1.1.

## Scope decision (raised here, as the spec's "Known risk" section requires)

The spec's known risk is real. I checked `pygents/utils.py:29-35`: `asyncgen_wrapper` runs `async for item in func(self, *args, **kwargs): yield item`. The inner generator lives only on the wrapper frame's value stack. `aclose()` on the wrapper throws `GeneratorExit` at the wrapper's `yield item`. That exits the `async for` without closing the inner generator. When the wrapper frame unwinds, the inner generator's refcount drops to zero and asyncio's `_asyncgen_finalizer_hook` schedules `loop.create_task(inner.aclose())` via `call_soon_threadsafe`. So right after `await gen.aclose()` returns, the inner `yielding()` has not run any cleanup yet: `_is_running` is still `True` and `stop_reason` is unset. Spec tests 1, 2, 3, 5 and 8 assert state "after `aclose()` returns", and test 8 says "`aclose()` must not return until that cleanup finishes". None of them can pass with a `turn.py`-only change unless they add loop-draining sleeps, and the spec forbids that as a way of hiding the gap.

Decision: this plan adds `pygents/utils.py` (the `safe_execution` async-generator branch only) and `tests/unit/test_utils.py` to this card's file scope, as Task 2. The change is three lines: wrap the inner generator in `contextlib.aclosing(...)`. No signature change, no new dependency (stdlib), no public API change. `Agent.run()` is also decorated with `@safe_execution` (`pygents/agent.py:443`), so after this change its inner generator is also closed synchronously on early exit. The sibling card c12afa98 needs exactly that ("reusable immediately, not after garbage collection", milestone Review Focus 3). This card still does not edit `pygents/agent.py`. If Task 2's full-suite run shows a failure in `tests/unit/test_agent.py` or `tests/integration/`, stop and escalate. Do not edit `agent.py` or those tests to make it pass. The reviewer of this card should explicitly confirm or reject this scope addition.

## Global Constraints

- Verification: `uv run pytest tests/` green; `uv run ruff format .` leaves no diff; `uv run ruff check .` passes (CI: `.github/workflows/tests.yml`, `.github/workflows/ruff.yml`). No typecheck step.
- No new dependencies. No public API removed. No signature changes. Version stays 0.6.8.
- Branch `m1/task-a-turn-that-is-closed-4554b946`, worktree `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-a-turn-that-is-closed-4554b946`, cut fresh from origin/master. Assume no other subtask's code is present.
- Test style: no pytest-asyncio and no async test functions. Every test is `def test_...():` whose async body runs through `asyncio.run(_body())`.
- Tier: every test in this plan is a unit test in `tests/unit/`. Turn behaviour goes in `tests/unit/test_turn.py`. The `safe_execution` behaviour goes in `tests/unit/test_utils.py`, the existing unit file for `pygents/utils.py`, which already holds `test_safe_execution_*`.
- Registry isolation: `tests/conftest.py` only resets `HookRegistry._global_hooks`. Tests that register hooks call `HookRegistry.clear()` first (existing pattern, e.g. `test_returning_before_run_hook_raises_sets_error_metadata_and_finally_runs`). Tool names are global. Each new test defines its own uniquely named tools inside the test and uses the `isolated_tool_registry` fixture (Task 1), which clears `ToolRegistry` for the test and restores it afterwards. A bare `ToolRegistry.clear()` is not safe here: nothing in `tests/` calls it today, and it would delete the module-level `@tool()` fixtures at the top of `tests/unit/test_turn.py` (and other modules' import-time tools) for every later test.
- Out of scope: everything in `pygents/agent.py`, re-queuing the interrupted turn, `Agent.close()`/context manager, owner-qualified hook names, version bump/release.

## Review Focus

1. **Cancelling a task that awaits `returning()` on a slow coroutine tool.** Expect `CancelledError` at the caller, `CANCELLED`, no `ON_ERROR`, `_is_running` False. Pinned by Task 1 tests (spec tests 6 and 7).
2. **A tool whose `finally` awaits before it finishes cleaning up.** `aclose()` must not return until that cleanup is done, and no tasks may be left. Pinned by Task 3 `test_yielding_when_consumer_closes_early_waits_for_slow_tool_cleanup` (spec test 8).
3. **The producer has already failed when the consumer closes** (the tool yielded one item then raised). Expect `CANCELLED` rather than `ERROR`, no `ON_ERROR`, and `aclose()` does not raise the tool's exception. Pinned by Task 3 `test_yielding_when_consumer_closes_after_tool_failed_reports_cancelled_not_error`. The producer-already-*finished* case is pinned by spec test 1, whose tool finishes before the first item is consumed.
4. **`aclose()` before the first item is requested.** Nothing should start: no `start_time`, no `stop_reason`, no tasks, `_is_running` False. Pinned by Task 3 `test_yielding_closed_before_first_item_never_starts_the_turn` (characterization test, passes before and after).
5. **The timeout path must still report `TIMEOUT`, not `CANCELLED`.** The new handler sits ahead of the timeout handler, and the timeout path itself cancels the producer. Pinned by the existing `test_yielding_times_out_sets_stop_reason_and_end_time`, `test_yielding_remaining_zero_branch` and `test_returning_times_out_sets_stop_reason_and_end_time`, which Task 3 Step 6 runs explicitly.

---

### Task 1: `returning()` reports CANCELLED when its task is cancelled

**Files:**
- Modify: `pygents/turn.py:265-272` (the `except` chain of `returning()`)
- Test: `tests/unit/test_turn.py` (import line 41, docstring decision table lines 16-21, append new section at end of file after `test_turn_tags_survive_serialization_roundtrip`)

**Interfaces:**
- Consumes: `Turn.returning()`, `StopReason.CANCELLED` (`pygents/turn.py:33`), `ToolRegistry` (`pygents/registry.py:48`), `HookRegistry.clear()`, `hook(TurnHook.X)`.
- Produces: pytest fixture `isolated_tool_registry` in `tests/unit/test_turn.py` (no return value; clears `ToolRegistry._registry` for the test and restores the saved dict after). Tasks 2 and 3 use it by name.

- [ ] **Step 1: Import `ToolRegistry` in the test module**

In `tests/unit/test_turn.py`, replace line 41:

```python
from pygents.registry import HookRegistry
```

with:

```python
from pygents.registry import HookRegistry, ToolRegistry
```

- [ ] **Step 2: Add R6 to the decision table docstring**

In `tests/unit/test_turn.py`, replace:

```python
  R5  Tool raises -> ERROR, ON_ERROR(e), re-raise, finally end_time
```

with:

```python
  R5  Tool raises -> ERROR, ON_ERROR(e), re-raise, finally end_time
  R6  Awaiting task cancelled -> CANCELLED, no ON_ERROR, CancelledError re-raised, finally end_time + ON_COMPLETE(CANCELLED)
```

- [ ] **Step 3: Write the failing tests**

Append to the end of `tests/unit/test_turn.py` (after `test_turn_tags_survive_serialization_roundtrip`):

```python


# ---------------------------------------------------------------------------
# Cancellation (R6, Y6-Y8) - tools are defined per test in an isolated registry
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_tool_registry():
    """Clear ToolRegistry for one test, then restore it.

    A bare ToolRegistry.clear() would drop the module-level tools above (and
    other test modules' tools), which are registered once at import time.
    """
    saved = dict(ToolRegistry._registry)
    ToolRegistry.clear()
    yield
    ToolRegistry._registry = saved


def test_returning_when_task_cancelled_sets_stop_reason_cancelled_and_propagates(
    isolated_tool_registry,
):
    async def _body():
        started = asyncio.Event()

        @tool()
        async def turn_cancel_returning_slow() -> int:
            started.set()
            await asyncio.sleep(10)
            return 1

        turn = Turn(turn_cancel_returning_slow)
        task = asyncio.create_task(turn.returning())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert turn.metadata.stop_reason == StopReason.CANCELLED
        assert turn.metadata.end_time is not None
        assert turn._is_running is False

    asyncio.run(_body())


def test_returning_when_task_cancelled_fires_on_complete_with_cancelled_not_on_error(
    isolated_tool_registry,
):
    HookRegistry.clear()
    events = []

    @hook(TurnHook.ON_COMPLETE)
    async def record_returning_cancel_complete(turn, stop_reason):
        events.append(("on_complete", stop_reason))

    @hook(TurnHook.ON_ERROR)
    async def record_returning_cancel_error(turn, exc):
        events.append(("on_error", type(exc).__name__))

    async def _body():
        started = asyncio.Event()

        @tool()
        async def turn_cancel_returning_hooks_slow() -> int:
            started.set()
            await asyncio.sleep(10)
            return 1

        turn = Turn(turn_cancel_returning_hooks_slow)
        task = asyncio.create_task(turn.returning())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_body())
    assert events == [("on_complete", StopReason.CANCELLED)]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_turn.py -k "returning_when_task_cancelled" -v`
Expected: 2 FAILED. The first fails with `assert None == <StopReason.CANCELLED: 'cancelled'>`, because `CancelledError` is a `BaseException` and skips `except Exception`, leaving `stop_reason` unset. The second fails with `assert [('on_complete', None)] == [('on_complete', <StopReason.CANCELLED: 'cancelled'>)]`.

- [ ] **Step 5: Implement the CancelledError branch in `returning()`**

In `pygents/turn.py`, inside `returning()`, replace:

```python
        except (asyncio.TimeoutError, TimeoutError):
            self.metadata.stop_reason = StopReason.TIMEOUT
            await self._run_hooks(TurnHook.ON_TIMEOUT)
            raise TurnTimeoutError(f"Turn timed out after {self.timeout}s") from None
        except Exception as e:
            self.metadata.stop_reason = StopReason.ERROR
            await self._run_hooks(TurnHook.ON_ERROR, e)
            raise
```

with:

```python
        except (asyncio.TimeoutError, TimeoutError):
            self.metadata.stop_reason = StopReason.TIMEOUT
            await self._run_hooks(TurnHook.ON_TIMEOUT)
            raise TurnTimeoutError(f"Turn timed out after {self.timeout}s") from None
        except asyncio.CancelledError:
            self.metadata.stop_reason = StopReason.CANCELLED
            raise
        except Exception as e:
            self.metadata.stop_reason = StopReason.ERROR
            await self._run_hooks(TurnHook.ON_ERROR, e)
            raise
```

(That exact 8-line block only appears in `returning()`. The `yielding()` timeout handler is formatted differently, so the match is unique. The `finally` block after it stays unchanged.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_turn.py -k "returning_when_task_cancelled" -v`
Expected: 2 PASSED.

- [ ] **Step 7: Run the whole Turn test module for regressions**

Run: `uv run pytest tests/unit/test_turn.py -v`
Expected: all PASSED (existing R1-R5 timeout/error tests unchanged).

- [ ] **Step 8: Commit**

```bash
git add pygents/turn.py tests/unit/test_turn.py
git commit -m "fix(turn): a cancelled returning() reports CANCELLED instead of skipping stop_reason"
```

---

### Task 2: `safe_execution` closes the wrapped async generator when it is closed

**Files:**
- Modify: `pygents/utils.py:1` (imports) and `pygents/utils.py:29-35` (`asyncgen_wrapper`)
- Test: `tests/unit/test_utils.py` (docstring decision table lines 6-9, new test after `test_safe_execution_asyncgen_raises_when_running` at line 102-117)
- Test: `tests/unit/test_turn.py` (docstring decision table, append after the Task 1 tests)

**Interfaces:**
- Consumes: `safe_execution` (`pygents/utils.py:26`), fixture `isolated_tool_registry` (Task 1).
- Produces: `safe_execution`'s async-generator wrapper closes the wrapped generator before its own `aclose()` returns. Task 3's tests depend on this, since they assert Turn state immediately after `await gen.aclose()`. Signature unchanged: `safe_execution(func: Callable[..., R]) -> Callable[..., R]`.

This task adds `pygents/utils.py` to the card's file scope. The "Scope decision" section above gives the reasoning. The reviewer must confirm it.

- [ ] **Step 1: Add SE4 to the utils decision table docstring**

In `tests/unit/test_utils.py`, replace:

```python
  SE3  self has no _is_running -> getattr returns False -> same as SE1
```

with:

```python
  SE3  self has no _is_running -> getattr returns False -> same as SE1
  SE4  async-gen func: closing the wrapper early closes the wrapped generator before aclose() returns
```

- [ ] **Step 2: Write the failing utils test**

In `tests/unit/test_utils.py`, insert directly after `test_safe_execution_asyncgen_raises_when_running` (which ends with `        asyncio.run(_())` at line 117):

```python


def test_safe_execution_asyncgen_closes_inner_generator_when_closed_early():
    closed = []

    @safe_execution
    async def gen_fn(self):
        try:
            yield 1
            yield 2
        finally:
            closed.append("inner closed")

    class Obj:
        _is_running = False

    async def _():
        agen = gen_fn(Obj())
        assert await agen.__anext__() == 1
        await agen.aclose()
        assert closed == ["inner closed"]

    asyncio.run(_())
```

- [ ] **Step 3: Add Y8 to the Turn decision table docstring**

In `tests/unit/test_turn.py`, replace:

```python
  Y5  Tool raises -> ERROR, ON_ERROR(e), finally end_time
```

with:

```python
  Y5  Tool raises -> ERROR, ON_ERROR(e), finally end_time
  Y8  After an early aclose() returns, _is_running is already False -> the turn can run again immediately
```

- [ ] **Step 4: Write the failing Turn re-run test (spec test 5)**

Append to the end of `tests/unit/test_turn.py` (after the Task 1 tests):

```python


def test_yielding_after_cancel_turn_can_run_again(isolated_tool_registry):
    async def _body():
        @tool()
        async def turn_rerun_after_close_gen():
            yield 1
            yield 2

        turn = Turn(turn_rerun_after_close_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert turn._is_running is False
        items = [x async for x in turn.yielding()]
        assert items == [1, 2]
        assert turn.metadata.stop_reason == StopReason.COMPLETED

    asyncio.run(_body())
```

- [ ] **Step 5: Run both tests to verify they fail**

Run: `uv run pytest tests/unit/test_utils.py::test_safe_execution_asyncgen_closes_inner_generator_when_closed_early tests/unit/test_turn.py::test_yielding_after_cancel_turn_can_run_again -v`
Expected: 2 FAILED. The utils test fails with `assert [] == ['inner closed']`, because the inner generator is only closed later by the loop's asyncgen finalizer. The turn test fails with `assert True is False` on `turn._is_running`, because the inner `yielding()` has not reached its `finally` yet.

- [ ] **Step 6: Implement `aclosing` in the wrapper**

In `pygents/utils.py`, replace line 1:

```python
import inspect
```

with:

```python
import contextlib
import inspect
```

Then replace:

```python
        async def asyncgen_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(self, "_is_running", False):
                raise SafeExecutionError(
                    f"Skipped <{func.__name__}> call because {self} is running."
                )
            async for item in func(self, *args, **kwargs):
                yield item
```

with:

```python
        async def asyncgen_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(self, "_is_running", False):
                raise SafeExecutionError(
                    f"Skipped <{func.__name__}> call because {self} is running."
                )
            # Close the wrapped generator ourselves: otherwise an early aclose()
            # of this wrapper leaves it to the loop's asyncgen finalizer, which
            # runs its cleanup on a later loop iteration.
            async with contextlib.aclosing(func(self, *args, **kwargs)) as agen:
                async for item in agen:
                    yield item
```

- [ ] **Step 7: Run both tests to verify they pass**

Run: `uv run pytest tests/unit/test_utils.py::test_safe_execution_asyncgen_closes_inner_generator_when_closed_early tests/unit/test_turn.py::test_yielding_after_cancel_turn_can_run_again -v`
Expected: 2 PASSED.

- [ ] **Step 8: Run the full suite (this wrapper also decorates `Agent.run()`)**

Run: `uv run pytest tests/`
Expected: all PASSED. If anything in `tests/unit/test_agent.py` or `tests/integration/` fails, STOP and escalate with the failure output. Do not change `pygents/agent.py` or those tests (sibling card c12afa98 owns them).

- [ ] **Step 9: Commit**

```bash
git add pygents/utils.py tests/unit/test_utils.py tests/unit/test_turn.py
git commit -m "fix(utils): safe_execution closes the wrapped async generator on early close"
```

---

### Task 3: `yielding()` cancels its producer and reports CANCELLED on close or cancel

**Files:**
- Modify: `pygents/turn.py:329-330` (insert a handler between `await producer` and `except (asyncio.TimeoutError, TimeoutError) as exc:` in `yielding()`)
- Test: `tests/unit/test_turn.py` (docstring decision table, append after the Task 2 test)

**Interfaces:**
- Consumes: fixture `isolated_tool_registry` (Task 1). `safe_execution` closes the inner generator synchronously (Task 2). `StopReason.CANCELLED`. `HookRegistry.clear()`, `hook(TurnHook.ON_COMPLETE | ON_ERROR | ON_TIMEOUT)`.
- Produces: `Turn.yielding()` behaviour. After `await gen.aclose()` returns, or after the cancelled consumer task is awaited: `metadata.stop_reason is StopReason.CANCELLED`, `metadata.end_time` is set, `ON_COMPLETE(turn, StopReason.CANCELLED)` has fired once, `_is_running is False`, and the producer task is done. Sibling card c12afa98 relies on this.

- [ ] **Step 1: Add Y6/Y7 to the decision table docstring**

In `tests/unit/test_turn.py`, replace:

```python
  Y8  After an early aclose() returns, _is_running is already False -> the turn can run again immediately
```

with:

```python
  Y6  Consumer closes early (aclose) -> producer cancelled and awaited, CANCELLED, no ON_ERROR/ON_TIMEOUT, GeneratorExit re-raised, finally end_time + ON_COMPLETE(CANCELLED)
  Y7  Consuming task cancelled -> same as Y6, CancelledError re-raised to the awaiter
  Y8  After an early aclose() returns, _is_running is already False -> the turn can run again immediately
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/unit/test_turn.py` (after `test_yielding_after_cancel_turn_can_run_again`):

```python


def test_yielding_when_consumer_closes_early_sets_stop_reason_cancelled(
    isolated_tool_registry,
):
    async def _body():
        # No awaits in the tool: the producer has already finished (all items
        # queued) by the time the first item is consumed, so this also covers
        # "producer already finished when the consumer closes".
        @tool()
        async def turn_close_early_multi_gen():
            yield 1
            yield 2
            yield 3

        turn = Turn(turn_close_early_multi_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert turn.metadata.stop_reason == StopReason.CANCELLED
        assert turn.metadata.end_time is not None
        assert turn._is_running is False

    asyncio.run(_body())


def test_yielding_when_consumer_closes_early_cancels_producer(isolated_tool_registry):
    events = []

    async def _body():
        @tool()
        async def turn_close_early_slow_gen():
            try:
                yield 1
                await asyncio.sleep(10)
                events.append("ran after close")
                yield 2
            finally:
                events.append("tool closed")

        turn = Turn(turn_close_early_slow_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert events == ["tool closed"]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(_body())
    assert events == ["tool closed"]


def test_yielding_when_consumer_closes_early_fires_on_complete_with_cancelled_not_on_error(
    isolated_tool_registry,
):
    HookRegistry.clear()
    events = []

    @hook(TurnHook.ON_COMPLETE)
    async def record_yielding_close_complete(turn, stop_reason):
        events.append(("on_complete", stop_reason))

    @hook(TurnHook.ON_ERROR)
    async def record_yielding_close_error(turn, exc):
        events.append(("on_error", type(exc).__name__))

    @hook(TurnHook.ON_TIMEOUT)
    async def record_yielding_close_timeout(turn):
        events.append(("on_timeout",))

    async def _body():
        @tool()
        async def turn_close_early_hooks_gen():
            yield 1
            await asyncio.sleep(10)
            yield 2

        turn = Turn(turn_close_early_hooks_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()

    asyncio.run(_body())
    assert events == [("on_complete", StopReason.CANCELLED)]


def test_yielding_when_consuming_task_cancelled_sets_stop_reason_cancelled_and_propagates(
    isolated_tool_registry,
):
    async def _body():
        @tool()
        async def turn_cancel_consumer_slow_gen():
            yield 1
            await asyncio.sleep(10)
            yield 2

        turn = Turn(turn_cancel_consumer_slow_gen)
        first_item = asyncio.Event()

        async def consume():
            async for _ in turn.yielding():
                first_item.set()

        task = asyncio.create_task(consume())
        # When this wakes, the consumer has already re-entered yielding() and
        # is blocked waiting for item 2.
        await first_item.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert turn.metadata.stop_reason == StopReason.CANCELLED
        assert turn.metadata.end_time is not None
        assert turn._is_running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(_body())


def test_yielding_when_consumer_closes_early_waits_for_slow_tool_cleanup(
    isolated_tool_registry,
):
    cleaned = []

    async def _body():
        @tool()
        async def turn_close_slow_cleanup_gen():
            try:
                yield 1
                await asyncio.sleep(10)
                yield 2
            finally:
                await asyncio.sleep(0.02)
                cleaned.append("cleanup finished")

        turn = Turn(turn_close_slow_cleanup_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert cleaned == ["cleanup finished"]
        assert turn.metadata.stop_reason == StopReason.CANCELLED
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(_body())


def test_yielding_when_consumer_closes_after_tool_failed_reports_cancelled_not_error(
    isolated_tool_registry,
):
    HookRegistry.clear()
    events = []

    @hook(TurnHook.ON_ERROR)
    async def record_yielding_failed_close_error(turn, exc):
        events.append(("on_error", type(exc).__name__))

    async def _body():
        # The producer yields 1, then raises and finishes before the consumer
        # resumes; aclose() must not surface that ValueError.
        @tool()
        async def turn_close_after_fail_gen():
            yield 1
            raise ValueError("boom after first item")

        turn = Turn(turn_close_after_fail_gen)
        gen = turn.yielding()
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert turn.metadata.stop_reason == StopReason.CANCELLED
        assert turn._is_running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(_body())
    assert events == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_turn.py -k "consumer_closes or consuming_task_cancelled" -v`
Expected: 6 FAILED:
- `..._sets_stop_reason_cancelled`: `assert None == <StopReason.CANCELLED: 'cancelled'>`.
- `..._cancels_producer`: `assert [] == ['tool closed']`, because the producer is still sleeping.
- `..._fires_on_complete_with_cancelled_not_on_error`: `assert [('on_complete', None)] == [('on_complete', <StopReason.CANCELLED: 'cancelled'>)]`.
- `..._consuming_task_cancelled_...`: `assert None == <StopReason.CANCELLED: 'cancelled'>`.
- `..._waits_for_slow_tool_cleanup`: `assert [] == ['cleanup finished']`.
- `..._after_tool_failed_reports_cancelled_not_error`: `assert None == <StopReason.CANCELLED: 'cancelled'>`.

If any of these fails with a different message (for example a `SafeExecutionError`, or state that looks like the inner generator was never closed), check that Task 2 is committed on this branch before going further.

- [ ] **Step 4: Implement the cancellation handler in `yielding()`**

In `pygents/turn.py`, inside `yielding()`, replace:

```python
                    aggregated.append(item)
                    yield item
                await producer
            except (asyncio.TimeoutError, TimeoutError) as exc:
```

with:

```python
                    aggregated.append(item)
                    yield item
                await producer
            except (GeneratorExit, asyncio.CancelledError):
                # Consumer closed us (aclose) or its task was cancelled: stop the
                # tool and wait for its cleanup. Never yield here.
                producer.cancel()
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass
                self.metadata.stop_reason = StopReason.CANCELLED
                raise
            except (asyncio.TimeoutError, TimeoutError) as exc:
```

Leave the timeout handler below it, the outer `except TurnTimeoutError` / `except Exception` and the outer `finally` exactly as they are. `GeneratorExit` and `CancelledError` are `BaseException`s, so the re-raise passes by `except Exception` and reaches the `finally`, which sets `end_time`, fires `ON_COMPLETE(CANCELLED)` and clears `_is_running`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_turn.py -k "consumer_closes or consuming_task_cancelled" -v`
Expected: 6 PASSED.

- [ ] **Step 6: Confirm the timeout and error paths are unchanged (Review Focus 5)**

Run: `uv run pytest "tests/unit/test_turn.py::test_yielding_times_out_sets_stop_reason_and_end_time" "tests/unit/test_turn.py::test_yielding_remaining_zero_branch" "tests/unit/test_turn.py::test_returning_times_out_sets_stop_reason_and_end_time" "tests/unit/test_turn.py::test_yielding_async_when_tool_raises_sets_stop_reason_error_and_propagates" -v`
Expected: 4 PASSED (TIMEOUT and ERROR, not CANCELLED).

- [ ] **Step 7: Add the close-before-start characterization test (Review Focus 4)**

Append to the end of `tests/unit/test_turn.py`:

```python


def test_yielding_closed_before_first_item_never_starts_the_turn(
    isolated_tool_registry,
):
    async def _body():
        @tool()
        async def turn_close_before_start_gen():
            yield 1

        turn = Turn(turn_close_before_start_gen)
        gen = turn.yielding()
        await gen.aclose()
        assert turn.metadata.start_time is None
        assert turn.metadata.stop_reason is None
        assert turn._is_running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(_body())
```

- [ ] **Step 8: Run it (characterization, expected to pass at once)**

Run: `uv run pytest tests/unit/test_turn.py::test_yielding_closed_before_first_item_never_starts_the_turn -v`
Expected: PASSED. This test pins existing behaviour: an unstarted generator's body never runs. It is not a RED test. If it fails, the cancellation handler or wrapper is doing work it should not do. Fix that. Do not change the test.

- [ ] **Step 9: Run the Turn module**

Run: `uv run pytest tests/unit/test_turn.py -v`
Expected: all PASSED.

- [ ] **Step 10: Commit**

```bash
git add pygents/turn.py tests/unit/test_turn.py
git commit -m "fix(turn): a closed or cancelled turn stops its tool and reports CANCELLED"
```

---

### Task 4: Full verification

**Files:**
- No new edits expected. Only formatter output, if any.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: a branch that passes CI's exact commands.

- [ ] **Step 1: Format**

Run: `uv run ruff format .`
Expected: `N files left unchanged`. If it reformats any of `pygents/turn.py`, `pygents/utils.py`, `tests/unit/test_turn.py` or `tests/unit/test_utils.py`, keep the result. If it touches any other file, revert that file with `git checkout -- <file>`, because pre-existing formatting drift is not this card's to fix.

- [ ] **Step 2: Lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Full test suite**

Run: `uv run pytest tests/`
Expected: all PASSED. If a failure is in `tests/unit/test_agent.py` or `tests/integration/`, stop and escalate (see Task 2 Step 8).

- [ ] **Step 4: Check the diff stays in scope**

Run: `git diff --stat origin/master...HEAD -- pygents tests`
Expected: exactly `pygents/turn.py`, `pygents/utils.py`, `tests/unit/test_turn.py`, `tests/unit/test_utils.py`. No `pygents/agent.py`, no version files.

- [ ] **Step 5: Commit formatter changes (only if Step 1 changed in-scope files)**

```bash
git add pygents/turn.py pygents/utils.py tests/unit/test_turn.py tests/unit/test_utils.py
git commit -m "style: ruff format"
```
