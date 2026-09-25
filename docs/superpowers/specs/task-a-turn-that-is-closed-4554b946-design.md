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
